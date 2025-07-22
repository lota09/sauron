'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : Update.py
  - Description      : Fetch and tell apart the new announcement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''
# -*- coding: utf-8 -*-

import os
import unicodedata

def IndexPrevious(titles,dept_id = None,file_path = None):
    
    #dept_id 지정시 버퍼파일 지정
    if dept_id:
        file_path = f"buffers/last-{dept_id}.txt"

    # 파일이 존재하지 않으면 그냥 가장 오래된
    if not os.path.exists(file_path):
        return None
    
    # 파일의 기존 첫 줄을 읽어옵니다.
    with open(file_path, 'r', encoding='utf-8') as file:
        previous_title = file.readline().strip()  # 첫 줄을 읽고 양쪽 공백을 제거합니다.
        #titles와 비교를 위해 NFD 정규화
        previous_title = unicodedata.normalize('NFD', previous_title)
        
    # 이전 제목과 일치하는 항목의 인덱스 반환
    try:
        prev_idx = titles.index(previous_title)
    except ValueError:
        return None

    return prev_idx


def UpdateLatest(new_title,dept_id = None,file_path = None):
    #dept_id 지정시 버퍼파일 지정
    if dept_id:
        file_path = f"buffers/last-{dept_id}.txt"

    #NFC 정규화 (자모 분리 방지)
    new_title = unicodedata.normalize('NFC', new_title)

    #부모 디렉터리 없으면 생성
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    #최신 제목 갱신
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(new_title + '\n')
        file.close()
    return 0

def UpdateState(dept_id,urls):
    file_path = f"buffers/last-{dept_id}.txt"

    #부모 디렉터리 없으면 생성
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            old_urls = set(line.strip() for line in f if line.strip())
            new_indices = [i for i, url in enumerate(urls) if url not in old_urls]
    except FileNotFoundError:
        new_indices = [0]

    # buffers/last-{dept_id}.txt 파일 갱신
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(urls))

    return new_indices