#!/usr/bin/python3

import subprocess
from flask.config import T
import serial
import redis
import time
import json
import threading
import psutil
import datetime
import random
import string
import signal
import os

# 同步系统时间
os.system("hwclock -s")

# 配置参数
SERIAL_PORT = '/dev/ttyS0'
BAUD_RATE = 115200

# 初始化串口
ser = serial.Serial(SERIAL_PORT, BAUD_RATE)
ser.write(b"Serial port is ready.\r\n")

# 连接Redis并进行密码认证
while True:
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, password='orangepi')
        r.ping()
        break
    except redis.ConnectionError as e:
        time.sleep(5)

def start_ffmpeg_process(resolution, bitrate):
    pid = r.get("ffmpeg_stream2_pid")
    if pid and psutil.pid_exists(int(pid)):
        return

    RTSP_URL = "rtsp://admin:abcd1234@192.168.137.123:554"
    bufsize = str(int(bitrate[:-1]) * 2) + bitrate[-1]  # 将bufsize设置为bitrate的两倍
    
    if resolution in ["1920x1080", "1280x720", "320x180"]:
        scale_command = f"scale_rkrga=w={resolution.split('x')[0]}:h={resolution.split('x')[1]}:format=nv12:afbc=1"
        ffmpeg_cmd = [
            "ffmpeg",
            "-hwaccel", "rkmpp",
            "-hwaccel_output_format", "drm_prime",
            "-afbc", "rga",
            "-rtsp_transport", "tcp",
            "-i", RTSP_URL,
            "-c:a", "copy",
            "-strict", "-2",
            "-vf", scale_command,
            "-c:v", "h264_rkmpp",
            "-rc_mode", "CBR",
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", bufsize,
            "-profile:v", "high",
            "-g:v", "30",
            "-f", "h264",
            "-"
        ]
    else:
        ffmpeg_cmd = [
            "ffmpeg",
            "-hwaccel", "rkmpp",
            "-rtsp_transport", "tcp",
            "-i", RTSP_URL,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", bufsize,
            "-s", resolution,
            "-r", "30",
            "-g", "30",  # 设置i帧间隔为30
            "-f", "h264",
            "-"
        ]
    
    print(ffmpeg_cmd)
    process = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.PIPE,
    )
    r.set(f"ffmpeg_stream2_pid", process.pid)
    
    # 从FFmpeg读取数据并写入串口
    try:
        while True:
            data = process.stdout.read(1)
            if not data:
                break
            # print(data)
            ser.write(data)
    except Exception as e:
        print("error", e)
    finally:
        process.terminate()

def start_recording(duration):
    pid = r.get("recording_pid")
    if pid and psutil.pid_exists(int(pid)):
        return

    rtsp_url = "rtsp://admin:abcd1234@192.168.137.123:554"
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    random_str = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    output_file = f"/home/orangepi/data_test/output_{now}_{random_str}_piece%d.mp4"

    ffmpeg_command = [
        "ffmpeg",
        "-y",  # 强制覆盖输出文件
        "-i", rtsp_url,
        "-c:v", "copy",
        "-f", "segment",
        "-segment_time", str(int(duration)),  # 将录制时间的单位改为分钟
        "-reset_timestamps", "1",
        output_file
    ]

    try:
        process = subprocess.Popen(ffmpeg_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        r.set("recording_pid", process.pid)
    except Exception as e:
        print("error", e)

def stop_recording():
    pid = r.get("recording_pid")
    if pid:
        try:
            process = psutil.Process(int(pid))
            process.send_signal(signal.SIGINT)
            process.wait()  # 等待进程终止
            r.delete("recording_pid")
        except Exception as e:
            print("error", e)

# 从串口读取数据并启动或停止FFmpeg进程
ffmpeg_thread = None

try:
    while True:
        line = ser.read_until(b"\r\n").decode("utf-8").strip()
        print(line)
        if not line:
            continue
        
        data = json.loads(line)
        command = data.get('command')
        
        if command == 'start':
            resolution = data.get('resolution')
            bitrate = data.get('bitrate')
            
            # 启动FFmpeg子线程
            ffmpeg_thread = threading.Thread(target=start_ffmpeg_process, args=(resolution, bitrate))
            ffmpeg_thread.start()
        
        elif command == 'stop':
            pid = r.get(f"ffmpeg_stream2_pid")
            if pid:
                try:
                    process = psutil.Process(int(pid))
                    for proc in process.children(recursive=True):
                        proc.kill()
                    process.kill()
                    if ffmpeg_thread and ffmpeg_thread.is_alive():
                        ffmpeg_thread.join(timeout=1)
                    r.delete(f"ffmpeg_stream2_pid")
                except Exception as e:
                    print("error", e)
        
        elif command == 'start_recording':
            duration = data.get('duration')
            start_recording(duration)

        elif command == 'sync_time':
            datetime = data.get('datetime')
            try:
                # 设置系统时间
                os.system(f'date -s "{datetime}"')
                
                # 设置RTC时间
                os.system(f'hwclock --set --date="{datetime}"')
            except Exception as e:
                print("error", e)
        
        elif command == 'stop_recording':
            stop_recording()

        elif command == 'shutdown':
            os.system("poweroff")
        else:
            print("Unknown command:", command)

except Exception as e:
    print("error", e)
    pass
finally:
    ser.close()