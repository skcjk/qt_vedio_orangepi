#!/usr/bin/python3

import subprocess
import serial
import time
import os
import socket
import crcmod
import threading
import psutil 

# 配置参数
SERIAL_PORT = '/dev/ttyS4'
BAUD_RATE = 38400
UDP_HOST1 = '192.168.1.1'
UDP_CMD_PORT1 = 10001
UDP_VEDIO_PORT1 = 10002
UDP_HOST2 = '192.168.1.1'
UDP_CMD_PORT2 = 10011
UDP_VEDIO_PORT2 = 10012
HEADER = b'\x48\x59\x43\x4C'  # 0x4859434C

# 同步系统时间
os.system("hwclock -s")

class ffmpegThread:
    def __init__(self):
        self.ffmpeg_process1 = None
        self.ffmpeg_process2 = None
        self.device2Ok = False
        self.resolution1 = "0"
        self.bitrate1 = "0"
        self.resolution2 = "0"
        self.bitrate2 = "0"
        self.now_resolution1 = "0"
        self.now_bitrate1 = "0"
        self.startFFmpeg2Flag = False


        self.crc16_modbus = crcmod.mkCrcFun(0x18005, initCrc=0xFFFF, rev=True, xorOut=0x0000)
        self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE)
        self.serBuffer = b''
        self.input_url = f"rtsp://localhost:8554/mystream"
        # self.input_url = f"rtsp://admin:abcd1234@192.168.1.123:554"

    
    def parse_protocol(self, data):
        if len(data) < 9:
            return None
        if data[0:4] != HEADER:
            return None
        addr_byte = data[4]
        cmd_type = data[5]
        length = data[6]
        if len(data) != length:
            return None
        payload = data[7:-2]
        crc_recv = int.from_bytes(data[-2:], byteorder='little')
        crc_calc = self.crc16_modbus(data[:-2])
        if crc_calc != crc_recv:
            return None
        return {
            'addr': addr_byte,
            'cmd': cmd_type,
            'length': length,
            'payload': payload,
            'crc': crc_recv
        }
    
    def receiveUDPCommandFrom1(self, buffer_size=4096):
        while True:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(('0.0.0.0', UDP_CMD_PORT1))
                while True:
                    data, addr = sock.recvfrom(buffer_size)
                    result = self.parse_protocol(data)
                    if result is not None:
                        if result['addr'] == 0x10 and result['cmd'] == 0x05:
                            os.system("poweroff")
            except Exception as e:
                time.sleep(1)
            finally:
                try:
                    sock.close()
                except:
                    pass

    def receiveUDPCommandFrom2(self, buffer_size=4096):
        while True:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(('0.0.0.0', UDP_CMD_PORT2))
                while True:
                    data, addr = sock.recvfrom(buffer_size)
                    result = self.parse_protocol(data)
                    if result is not None:
                        if result['addr'] == 0x10 and result['cmd'] == 0x01:
                            if len(result['payload']) == 1:
                                if result['payload'][0] == 0x00:
                                    self.device2Ok = True
                                    print("OK")
                        if result['addr'] == 0x10 and result['cmd'] == 0x02 and self.device2Ok == True:
                            if len(result['payload']) == 1:
                                rate_data = result['payload'][0]
                                rate_map = {0x05: '80k', 0x06: '40k', 0x07: '20k', 0x08:'10k'}
                                resolution_map = {
                                    0x05: '320x240',
                                    0x06: '320x240',
                                    0x07: '256x128',
                                    0x08: '256x128'
                                }
                                self.bitrate2 = rate_map.get(rate_data, '0')
                                self.resolution2 = resolution_map.get(rate_data, '0')
                                self.startFFmpeg2Flag = True
                                print(self.bitrate2, self.resolution2)
                        if result['addr'] == 0x10 and result['cmd'] == 0x05:
                            os.system("poweroff")
            except Exception as e:
                time.sleep(1)
            finally:
                try:
                    sock.close()
                except:
                    pass

    def receiveSerialCommand(self):
        try:
            while True:
                # 读取数据
                try:
                    data = self.ser.read(self.ser.in_waiting or 1)
                    print(data)
                    if data:
                        self.serBuffer += data
                        # 按协议解析数据帧
                        while len(self.serBuffer) >= 10:  # 最小帧长10字节
                            if self.serBuffer[:4] != HEADER:
                                # 帧头不匹配，丢弃第一个字节
                                self.serBuffer = self.serBuffer[1:]
                                continue
                            # 帧头匹配，检查剩余长度
                            if len(self.serBuffer) < 10:
                                break
                            result = self.parse_protocol(self.serBuffer)
                            if result is not None:
                                if result['addr'] == 0x10 and result['cmd'] == 0x02:
                                    if len(result['payload']) == 1:
                                        rate_data = result['payload'][0]
                                        rate_map = {0x01: '8M', 0x02: '4M', 0x03: '2M', 0x04: '1M'}
                                        resolution_map = {
                                            0x01: '1920x1080',
                                            0x02: '1920x1080',
                                            0x03: '1280x720',
                                            0x04: '1280x720'
                                        }
                                        self.bitrate1 = rate_map.get(rate_data, '0')
                                        self.resolution1 = resolution_map.get(rate_data, '0')
                                        self.ser.write(b'\x82')
                                self.serBuffer = self.serBuffer[10:]
                except Exception as e:
                    print("error", e)
        except Exception as e:
            print("error", e)


    def kill_process(self, proc):
        # 使用psutil杀死进程及其子进程
        if proc is not None:
            try:
                p = psutil.Process(proc.pid)
                for child in p.children(recursive=True):
                    child.kill()
                p.kill()
            except Exception as e:
                print("kill_process error:", e)

    def manageFFmpegProcess1(self):
        while True:
            if (self.bitrate1 != self.now_bitrate1 or self.resolution1 != self.now_resolution1):
                # 如果进程正在运行，先kill
                if self.ffmpeg_process1 and self.ffmpeg_process1.poll() is None:
                    self.kill_process(self.ffmpeg_process1)
                    self.ffmpeg_process1 = None
                bufsize = str(int(self.bitrate1[:-1]) * 2) + self.bitrate1[-1]
                if self.resolution1 in ["1920x1080", "1280x720", "320x180"]:
                    scale_command = f"-vf scale_rkrga=w={self.resolution1.split('x')[0]}:h={self.resolution1.split('x')[1]}:format=nv12:afbc=1"
                    ffmpeg_command = f"ffmpeg -hwaccel rkmpp -hwaccel_output_format drm_prime -afbc rga -rtsp_transport tcp -i {self.input_url} -c:a copy -strict -2 {scale_command} -c:v h264_rkmpp -rc_mode CBR -b:v {self.bitrate1} -maxrate {self.bitrate1} -bufsize {bufsize} -profile:v high -g:v 50 -f h264 -"
                else:
                    ffmpeg_command = f"ffmpeg -hwaccel rkmpp -rtsp_transport tcp -i {self.input_url} -c:v libx264 -x264-params keyint=50:scenecut=0:repeat-headers=1 -preset ultrafast -tune zerolatency -b:v {self.bitrate1} -s {self.resolution1} -r 30 -f h264 -"
                print(ffmpeg_command)
                self.now_bitrate1 = self.bitrate1
                self.now_resolution1 = self.resolution1
                self.ffmpeg_process1 = subprocess.Popen(ffmpeg_command, 
                                                        shell=True, 
                                                        stdout=subprocess.PIPE, 
                                                        stderr=subprocess.DEVNULL)
            time.sleep(1)

    def manageFFmpegProcess2(self):
        while True:
            if (self.startFFmpeg2Flag == True):
                # 如果进程正在运行，先kill
                if self.ffmpeg_process2 and self.ffmpeg_process2.poll() is None:
                    self.kill_process(self.ffmpeg_process2)
                    self.ffmpeg_process2 = None
                bufsize = str(int(self.bitrate2[:-1]) * 2) + self.bitrate2[-1]
                if self.resolution2 in ["1920x1080", "1280x720", "320x180"]:
                    scale_command = f"-vf scale_rkrga=w={self.resolution2.split('x')[0]}:h={self.resolution2.split('x')[1]}:format=nv12:afbc=1"
                    ffmpeg_command = f"ffmpeg -hwaccel rkmpp -hwaccel_output_format drm_prime -afbc rga -rtsp_transport tcp -i {self.input_url} -c:a copy -strict -2 {scale_command} -c:v h264_rkmpp -rc_mode CBR -b:v {self.bitrate2} -maxrate {self.bitrate2} -bufsize {bufsize} -profile:v high -g:v 50 -f h264 -"
                else:
                    ffmpeg_command = f"/home/orangepi/x265_ffmpeg/ffmpeg/bin/ffmpeg -rtsp_transport tcp -i {self.input_url} -c:v libx264 -x264-params keyint=50:scenecut=0:repeat-headers=1:intra-refresh=1 -preset ultrafast -tune zerolatency -b:v {self.bitrate2} -s {self.resolution2} -t 10 -r 30 -f h264 -"
                print(ffmpeg_command)
                self.startFFmpeg2Flag = False
                self.ffmpeg_process2 = subprocess.Popen(ffmpeg_command, 
                                                        shell=True, 
                                                        stdout=subprocess.PIPE, 
                                                        stderr=subprocess.DEVNULL)   
            time.sleep(1)

    def pushH264ToUDP1(self):
        while True:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            print(f"UDP client sending to {UDP_HOST2}:{UDP_VEDIO_PORT2}")
            try:
                while True:
                    if self.ffmpeg_process1 and self.ffmpeg_process1.stdout:
                        data = self.ffmpeg_process1.stdout.read(4096)
                        if not data:
                            break
                        client.sendto(data, (UDP_HOST1, UDP_VEDIO_PORT1))
                    else:
                        time.sleep(0.1)
            except Exception as e:
                print("error", e)
            finally:
                client.close()
                if self.ffmpeg_process1:
                    self.kill_process(self.ffmpeg_process1)
                    self.ffmpeg_process1 = None
                    self.now_bitrate1 = "0"
                    self.now_resolution1 = "0"

    def pushH264ToUDP2(self):
        while True:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            print(f"UDP client sending to {UDP_HOST2}:{UDP_VEDIO_PORT2}")
            try:
                while True:
                    if self.ffmpeg_process2 and self.ffmpeg_process2.stdout:
                        data = self.ffmpeg_process2.stdout.read(1)
                        if not data:
                            break
                        client.sendto(data, (UDP_HOST2, UDP_VEDIO_PORT2))
                    else:
                        time.sleep(0.1)
            except Exception as e:
                print("error", e)
            finally:
                client.close()
                if self.ffmpeg_process2:
                    self.kill_process(self.ffmpeg_process2)
                    self.ffmpeg_process2 = None

    def start(self):
        threading.Thread(target=self.receiveUDPCommandFrom1, daemon=True).start()
        threading.Thread(target=self.receiveUDPCommandFrom2, daemon=True).start()
        threading.Thread(target=self.receiveSerialCommand, daemon=True).start()
        threading.Thread(target=self.manageFFmpegProcess1, daemon=True).start()
        threading.Thread(target=self.manageFFmpegProcess2, daemon=True).start()
        threading.Thread(target=self.pushH264ToUDP1, daemon=True).start()
        threading.Thread(target=self.pushH264ToUDP2, daemon=True).start()
        while True:
            time.sleep(10)

if __name__ == "__main__":
    ffmpeg_thread = ffmpegThread()
    ffmpeg_thread.start()