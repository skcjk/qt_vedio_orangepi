from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import psutil
import os
import datetime
import random
import string
import redis
import time
import signal

app = Flask(__name__)
CORS(app)  # 允许所有来源的跨域请求

# 连接Redis并进行密码认证
while True:
    try:
        r = redis.Redis(host='localhost', port=6379, db=0, password='orangepi')
        r.ping()
        break
    except redis.ConnectionError as e:
        time.sleep(5)

@app.route('/start/<stream>', methods=['POST'])
def start_ffmpeg(stream):
    data = request.get_json()
    resolution = data.get('resolution')
    bitrate = data.get('bitrate')
    
    # 构建ffmpeg命令
    input_url = f"rtsp://admin:abcd1234@192.168.137.123:554"
    output_url = f"rtsp://localhost:8554/{stream}"
    bufsize = str(int(bitrate[:-1]) * 2) + bitrate[-1]  # 将bufsize设置为bitrate的两倍
    
    if resolution in ["1920x1080", "1280x720", "320x180"]:
        scale_command = f"-vf scale_rkrga=w={resolution.split('x')[0]}:h={resolution.split('x')[1]}:format=nv12:afbc=1"
        ffmpeg_command = f"ffmpeg -hwaccel rkmpp -hwaccel_output_format drm_prime -afbc rga -rtsp_transport tcp -i {input_url} -c:a copy -strict -2 {scale_command} -c:v h264_rkmpp -rc_mode CBR -b:v {bitrate} -maxrate {bitrate} -bufsize {bufsize} -profile:v high -g:v 50 -f rtsp -rtsp_transport tcp {output_url}"
    else:
        ffmpeg_command = f"ffmpeg -hwaccel rkmpp -rtsp_transport tcp -i {input_url} -c:v libx264 -preset ultrafast -tune zerolatency -b:v {bitrate} -s {resolution} -r 30 -f rtsp -rtsp_transport tcp {output_url}"
    
    try:
        # 运行ffmpeg命令
        process = subprocess.Popen(ffmpeg_command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        r.set(f"ffmpeg_{stream}_pid", process.pid)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/stop/<stream>', methods=['POST'])
def stop_ffmpeg(stream):
    pid = r.get(f"ffmpeg_{stream}_pid")
    if pid:
        try:
            # 使用psutil终止进程
            process = psutil.Process(int(pid))
            for proc in process.children(recursive=True):
                proc.kill()
            process.kill()
            r.delete(f"ffmpeg_{stream}_pid")
            return jsonify({'status': 'stopped'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
    else:
        return jsonify({'status': 'not running'})

@app.route('/status', methods=['GET'])
def status_ffmpeg():
    statuses = {}
    for stream in ['stream1', 'stream2']:
        pid = r.get(f"ffmpeg_{stream}_pid")
        if pid:
            process = psutil.Process(int(pid))
            statuses[stream] = 'running' if process.is_running() else 'not running'
        else:
            statuses[stream] = 'not running'
    return jsonify(statuses)

@app.route('/sync_time', methods=['POST'])
def sync_time():
    data = request.get_json()
    datetime = data.get('datetime')
    try:
        # 设置系统时间
        command = f'date -s "{datetime}"'
        subprocess.run(command, shell=True, check=True)
        
        # 设置RTC时间
        rtc_command = f'hwclock --set --date="{datetime}"'
        subprocess.run(rtc_command, shell=True, check=True)
        
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/start_recording', methods=['POST'])
def start_recording():
    pid = r.get("recording_pid")
    if pid and psutil.pid_exists(int(pid)):
        return jsonify({'status': 'already recording'})
    
    data = request.get_json()
    duration = data.get('duration')
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
        return jsonify({'status': 'recording started'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/stop_recording', methods=['POST'])
def stop_recording():
    pid = r.get("recording_pid")
    if pid:
        try:
            process = psutil.Process(int(pid))
            process.send_signal(signal.SIGINT)
            process.wait()  # 等待进程终止
            r.delete("recording_pid")
            return jsonify({'status': 'recording stopped'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
    else:
        return jsonify({'status': 'not recording'})

@app.route('/recording_status', methods=['GET'])
def recording_status():
    pid = r.get("recording_pid")
    status = '录制中' if pid and psutil.pid_exists(int(pid)) else '未录制'
    return jsonify({'status': status})

@app.route('/shutdown', methods=['POST'])
def shutdown():
    try:
        subprocess.run(["poweroff"], check=True)
        return jsonify({'status': 'shutdown initiated'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/reboot', methods=['POST'])
def reboot():
    try:
        subprocess.run(["reboot"], check=True)
        return jsonify({'status': 'reboot initiated'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)