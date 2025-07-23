'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : main.py
  - Description      : Top of the project
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.23 : Initial release
*******************************************************************'''
# -*- coding: utf-8 -*-

import Overview
from DeptInfo import DEPTS
#import Notify
#import KakaoTalk
import DiscordMsg
import Errors
import Update
from datetime import datetime

import time
import sys

#exceptions
from requests.exceptions import ConnectionError
from http.client import RemoteDisconnected

MAX_RETRIES = 5  # 최대 재시도 횟수
RETRY_DELAY = 5  # 재시도 간격 (초)
TIMESTAMP_FILE = "buffers/update_timestamp.txt"

CURRENT_DEPT = None

def main():
    global CURRENT_DEPT

    for dept in DEPTS:

        CURRENT_DEPT = dept.dept_id

        #공지 정보 가져오기, 최신 공지 비교
        notice_list = Overview.UpdateNotice(dept)
        if notice_list is None:
            continue

        for notice_data in notice_list:

            #최신 공지 전달
            DiscordMsg.SendEmbedMessage(notice_data)
            
            #최신 공지 갱신
            #Update.UpdateLatest(notice_list['title'],dept.dept_id)



    #최신화 시간 갱신
    formatted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S") # format : 2024-12-24 14:35:22
    Update.UpdateLatest(formatted_time,file_path=TIMESTAMP_FILE)

    return 0
        
if __name__ == "__main__":
    #연결 오류 발생시 반복
    for i in range(MAX_RETRIES):
        try:
            main()
            sys.exit(0)
        #연결 오류 발생시 재시도
        except (ConnectionError, RemoteDisconnected):
            time.sleep(RETRY_DELAY)
            continue
        #디스코드 에러 포함 여러 문제 발생시 카카오톡 알림
        except Exception as e:
            debug_message = Errors.InfoCollect(e,CURRENT_DEPT)
            #KakaoTalk.SendDebugMessage(debug_message,RECV_UUID)
            DiscordMsg.SendDebugMessage(debug_message)
            print(debug_message)
            raise e
        
    print(f"Connection Failed After {MAX_RETRIES} tries")
    sys.exit(1)