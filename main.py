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
import Notify
import KakaoTalk
from Update import UpdateLatest

import time
import sys

#exceptions
from requests.exceptions import ConnectionError
from http.client import RemoteDisconnected

MAX_RETRIES = 5  # 최대 재시도 횟수
RETRY_DELAY = 5  # 재시도 간격 (초)

def main():
    func_overview=[
        Overview.UpdateUsaint,
        Overview.UpdateDisu,
        Overview.UpdateDisuBold,
        Overview.UpdateEco,
        Overview.UpdateEcoBold
    ]

    func_content=[
        Content.FetchUsaint,
        Content.FetchDisu,
        Content.FetchDisu,
        Content.FetchEco,
        Content.FetchEco,
    ]
    
    buffer_files=list(Overview.BUFFER_FILES.values())

    receiver_uuids=["jL-Iu4K3hbeEqJqomaiZr5iulrqLuoy-jrqD7A"]

    for func_idx, func in enumerate(func_overview):
        
        if (components := func()) is None:
            continue
        
        content= func_content[func_idx](components['url'])
        components['summary']= ''
        
        if (content):
            components['summary']= ClovaSummary.Summarize(f"제목:{components['title']}\n내용:\n{content}")
        
        Notify.Email(components)
        KakaoTalk.SendFriendMessage(components,receiver_uuids)
        
        UpdateLatest(components['title'],buffer_files[func_idx])
        
    return 0
        
if __name__ == "__main__":
    for i in range(MAX_RETRIES):
        try:
            main()
            sys.exit(0)
        except (ConnectionError, RemoteDisconnected):
            time.sleep(RETRY_DELAY)
            continue
        except:
            sys.exit(1)
        
    print(f"Connection Failed After {MAX_RETRIES} tries")
    sys.exit(1)
