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

        for i in range(MAX_RETRIES):
            
            #공지 정보 가져오기, 최신 공지 비교
            if (components := Overview.UpdateFetch(dept)) is None:
                break

            #최신 공지 전달
            #Notify.Email(components)
            DiscordMsg.SendEmbedMessage(components)
            #KakaoTalk.SendFriendMessage(components,RECV_UUID)
            
            #최신 공지 갱신
            Update.UpdateLatest(components['title'],dept.dept_id)

            #가장 최신항목이면 다음 dept
            if (components['latest']):
                break
        else:
            raise IndexError(f"Announcement Still Outdated After {MAX_RETRIES} Fetchs. \nOutdated Data :{components}")


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
        #카카오톡 에러 발생시 디스코드는 문제 없을 가능성이 큼
        except Errors.KakaoTalkError as e:
            debug_message = Errors.InfoCollect(e,CURRENT_DEPT)
            DiscordMsg.SendDebugMessage(debug_message)
            print(debug_message)
            raise e
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