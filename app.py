from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess

app = Flask(__name__)
CORS(app)  # 允许所有来源的跨域请求


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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)