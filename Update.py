'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : Update.py
  - Description      : Fetch and tell apart the new announcement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''
# -*- coding: utf-8 -*-

import os
#import unicodedata

def CheckLatest(new_title,file_path):
    
    # 파일이 존재하지 않으면 입력된 제목이 최신
    if not os.path.exists(file_path):
        return True
    
    # 파일의 기존 첫 줄을 읽어옵니다.
    with open(file_path, 'r', encoding='utf-8') as file:
        previous_title = file.readline().strip()  # 첫 줄을 읽고 양쪽 공백을 제거합니다.
        
    # 비교하여 다르면 입력된 제목이 최신
    if previous_title != new_title:
        return True
    
    return False

def UpdateLatest(new_title,file_path):
    
    #new_title = unicodedata.normalize('NFC', new_title) #이걸 하면 메모장에서 자모 분리 문제가 해결되는 대신 CheckLatest에서도 똑같이 이걸 해줘야함.
    #부모 디렉터리 없으면 생성
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    #최신 제목 갱신
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(new_title + '\n')
        file.close()
    return 0