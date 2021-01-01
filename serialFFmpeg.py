#!/usr/bin/python3

import subprocess
import serial
import redis
import time
import psutil
import os
import socket
import crcmod
import threading

UDP_HOST = '192.168.1.1'
UDP_PORT = 10002

# 同步系统时间
os.system("hwclock -s")

crc16_modbus = crcmod.mkCrcFun(0x18005, initCrc=0xFFFF, rev=True, xorOut=0x0000)

# 配置参数
SERIAL_PORT = '/dev/ttyS4'
BAUD_RATE = 38400

# 初始化串口
ser = serial.Serial(SERIAL_PORT, BAUD_RATE)

# 帧头定义
HEADER = b'\x48\x59\x43\x4C'  # 0x4859434C

# 缓冲区初始化
buffer = b''

# 连接Redis并进行密码认证
while True:
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, password='orangepi')
        r.ping()
        break
    except redis.ConnectionError as e:
        time.sleep(5)

def start_ffmpeg_process(bitrate, resolution="1920x1080"):
    pid = r.get("ffmpeg_stream1_pid")
    if pid and psutil.pid_exists(int(pid)):
        # 判断分辨率和码率是否一致
        old_bitrate = r.get("ffmpeg_stream1_bitrate")
        old_resolution = r.get("ffmpeg_stream1_resolution")
        if (old_bitrate is not None and old_bitrate.decode() != bitrate) or (old_resolution is not None and old_resolution.decode() != resolution):
            stop_ffmpeg()
        else:
            print("ffmpeg is running with same parameters, no need to restart")
            return
    else:
        print("ffmpeg is not running, starting a new process")
    # 构建ffmpeg命令
    # input_url = f"rtsp://admin:abcd1234@192.168.1.123:554"
    input_url = f"rtsp://localhost:8554/mystream"
    output_url = f"rtsp://localhost:8554/stream1"
    bufsize = str(int(bitrate[:-1]) * 2) + bitrate[-1]  # 将bufsize设置为bitrate的两倍
    if resolution in ["1920x1080", "1280x720", "320x180"]:
        scale_command = f"-vf scale_rkrga=w={resolution.split('x')[0]}:h={resolution.split('x')[1]}:format=nv12:afbc=1"
        ffmpeg_command = f"ffmpeg -hwaccel rkmpp -hwaccel_output_format drm_prime -afbc rga -rtsp_transport tcp -i {input_url} -c:a copy -strict -2 {scale_command} -c:v h264_rkmpp -rc_mode CBR -b:v {bitrate} -maxrate {bitrate} -bufsize {bufsize} -profile:v high -g:v 50 -f h264 -"
    else:
        ffmpeg_command = f"ffmpeg -hwaccel rkmpp -rtsp_transport tcp -i {input_url} -c:v libx264 -x264-params keyint=50:scenecut=0:repeat-headers=1 -preset ultrafast -tune zerolatency -b:v {bitrate} -s {resolution} -r 30 -f h264 -"
    try:
        print(ffmpeg_command)
        global process
        process = subprocess.Popen(ffmpeg_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        r.set(f"ffmpeg_stream1_pid", process.pid)
        r.set(f"ffmpeg_stream1_bitrate", bitrate)
        r.set(f"ffmpeg_stream1_resolution", resolution)
    except Exception as e:
        print('error:', e)
        pass

def stop_ffmpeg():
    pid = r.get(f"ffmpeg_stream1_pid")
    if pid:
        try:
            # 使用psutil终止进程
            process = psutil.Process(int(pid))
            for proc in process.children(recursive=True):
                proc.kill()
            process.kill()
            r.delete(f"ffmpeg_stream1_pid")
            r.delete(f"ffmpeg_stream1_bitrate")
            r.delete(f"ffmpeg_stream1_resolution")
        except Exception as e:
            pass
    else:
        return

def udp_send_thread():
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"UDP client sending to {UDP_HOST}:{UDP_PORT}")
    try:
        while True:
            if 'process' in globals() and process and process.stdout:
                data = process.stdout.read(4096)
                if not data:
                    break
                client.sendto(data, (UDP_HOST, UDP_PORT))
            else:
                time.sleep(0.1)
    except Exception as e:
        print("error", e)
    finally:
        if 'process' in globals() and process:
            process.terminate()
        client.close()

# 启动UDP线程
udp_thread = threading.Thread(target=udp_send_thread, daemon=True)
udp_thread.start()
start_ffmpeg_process('8M', '1920x1080')

try:
    while True:
        # 读取数据
        try:
            data = ser.read(ser.in_waiting or 1)
            if data:
                buffer += data
                print(buffer)
                # 按协议解析数据帧
                while len(buffer) >= 10:  # 最小帧长10字节
                    if buffer[:4] != HEADER:
                        # 帧头不匹配，丢弃第一个字节
                        buffer = buffer[1:]
                        continue
                    # 帧头匹配，检查剩余长度
                    if len(buffer) < 10:
                        break
                    dev_addr = buffer[4]
                    cmd_type = buffer[5]
                    frame_len = buffer[6]
                    rate_data = buffer[7]
                    crc_recv = buffer[8:10]
                    # 检查协议字段
                    if dev_addr != 0x10 or cmd_type != 0x02 or frame_len != 0x0A:
                        buffer = buffer[1:]
                        continue
                    # 计算CRC16-modebus
                    crc_calc = crc16_modbus(buffer[:8])
                    crc_calc_bytes = crc_calc.to_bytes(2, 'little')
                    print('crc:', hex(crc_calc))
                    if crc_recv != crc_calc_bytes:
                        buffer = buffer[1:]
                        continue
                    # 速率数据解析
                    rate_map = {0x01: '8M', 0x02: '4M', 0x03: '2M', 0x04: '1M', 
                                0x05: '80k', 0x06: '40k', 0x07: '20k', 0x08:'10k'}
                    rate_resolution_map = {
                        '8M': '1920x1080',
                        '4M': '1920x1080',
                        '2M': '1280x720',
                        '1M': '1280x720',
                        '80k': '320x240',
                        '40k': '320x240',
                        '20k': '256x128',
                        '10k': '256x128'
                    }
                    rate_str = rate_map.get(rate_data, '未知')
                    resolution = rate_resolution_map.get(rate_str, '1920x1080')
                    # print(f"收到有效帧: 设备地址={dev_addr:02X}, 命令类型={cmd_type:02X}, 帧长度={frame_len}, 速率={rate_str}, 分辨率={resolution}, CRC={crc_recv.hex().upper()}")
                    start_ffmpeg_process(rate_str, resolution)
                    buffer = buffer[10:]
                    ser.write(b'\x82')
        except Exception as e:
            print("error", e)
except Exception as e:
    print("error", e)
finally:
    ser.close()