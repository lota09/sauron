'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : CheckAlive.py
  - Description      : 사우론의 눈 작동상태 확인 (다른 모듈과 다르게 쉘에 의해 독립적으로 실행)
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.23 : Initial release
*******************************************************************'''

import os
import sys
import subprocess
from datetime import datetime

TIMESTAMP_FILE = "buffers/update_timestamp.txt"
VERIFY_DEVICE= "1511c751-1e7e-41bf-b344-aec76cbeab53"

def IsToday(file_path):
    # 파일이 존재하지 않으면 False 반환
    if not os.path.exists(file_path):
        return False

    # 파일의 첫 줄을 읽어옵니다.
    with open(file_path, 'r', encoding='utf-8') as file:
        recorded_time = file.readline().strip()

    try:
        # 기록된 시간을 datetime 객체로 변환
        recorded_date = datetime.strptime(recorded_time, "%Y-%m-%d %H:%M:%S").date()
    except ValueError:
        # 파일 형식이 잘못되었으면 False 반환
        return False

    # 오늘 날짜와 비교
    today_date = datetime.now().date()
    return recorded_date == today_date
    
if __name__ == "__main__":
    if not IsToday(TIMESTAMP_FILE):
        print("Eye of Sauron Died.")
        sys.exit(0)
    
    subprocess.run(["smartthings", "devices:commands", VERIFY_DEVICE, "switch:off"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(0)
