'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : main.py
  - Description      : Top of the project
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.23 : Initial release
*******************************************************************'''
# -*- coding: utf-8 -*-

import Overview
import Content
import ClovaSummary
#import Notify
import KakaoTalk
import DiscordMsg
import Errors
from Update import UpdateLatest
from datetime import datetime

import time
import sys

#exceptions
from requests.exceptions import ConnectionError
from http.client import RemoteDisconnected

MAX_RETRIES = 5  # 최대 재시도 횟수
RETRY_DELAY = 5  # 재시도 간격 (초)
RECV_UUID=["jL-Iu4K3hbeEqJqomaiZr5iulrqLuoy-jrqD7A"] #카카오톡 수신자 UUID 목록
TIMESTAMP_FILE = "buffers/update_timestamp.txt"

def main():
    func_overview=[
        Overview.UpdateUsaint,
        #Overview.UpdateDisu,
        Overview.UpdateDisuBold,
        #Overview.UpdateEco,
        Overview.UpdateEcoBold
    ]

    func_content=[
        Content.FetchUsaint,
        #Content.FetchDisu,
        Content.FetchDisu,
        #Content.FetchEco,
        Content.FetchEco,
    ]
    
    buffer_files=list(Overview.BUFFER_FILES.values()) #딕셔너리 그대로 가져오기 예정 #buffer_files = Overview.BUFFER_FILES

    for func_idx, func in enumerate(func_overview):

        for i in range(MAX_RETRIES):

            #공지 개요 가져오기, 최신 공지 비교
            if (components := func()) is None:
                break
            
            #공지 내용 가져오기
            content= func_content[func_idx](components['url'])
            
            #클로바 요약
            components['summary']= ''
            if (content):
                components['summary']= ClovaSummary.Summarize(f"제목:{components['title']}\n내용:\n{content}")
            
            #최신 공지 전달
            #Notify.Email(components)
            DiscordMsg.SendEmbedMessage(components)
            KakaoTalk.SendFriendMessage(components,RECV_UUID)
            
            #최신 공지 갱신
            UpdateLatest(components['title'],buffer_files[func_idx])

            #
            if (components['latest']):
                break
        else:
            raise IndexError(f"Announcement Still Outdated After {MAX_RETRIES} Fetchs. \nOutdated Data :{components}")


    #최신화 시간 갱신
    formatted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S") # format : 2024-12-24 14:35:22
    UpdateLatest(formatted_time,TIMESTAMP_FILE)

    return 0
        
if __name__ == "__main__":
    for i in range(MAX_RETRIES):
        try:
            main()
            sys.exit(0)
        #카카오톡 에러 발생시 디스코드는 문제 없을 가능성이 큼
        except Errors.KakaoTalkError as e:
            debug_message = Errors.InfoCollect(e)
            DiscordMsg.SendDebugMessage(debug_message)
            print(debug_message)
            raise e
        #연결 오류 발생시 재시도
        except (ConnectionError, RemoteDisconnected):
            time.sleep(RETRY_DELAY)
            continue
        #디스코드 에러 포함 여러 문제 발생시 카카오톡 알림
        except Exception as e:
            debug_message = Errors.InfoCollect(e)
            KakaoTalk.SendDebugMessage(debug_message,RECV_UUID)
            DiscordMsg.SendDebugMessage(debug_message)
            print(debug_message)
            raise e
        
    print(f"Connection Failed After {MAX_RETRIES} tries")
    sys.exit(1)