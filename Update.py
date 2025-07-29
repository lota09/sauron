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

def UpdateState(dept_id,urls,urls_p2=[]):
    file_path = f"buffers/last-{dept_id}.txt"

    #부모 디렉터리 없으면 생성
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            old_urls = set(line.strip() for line in f if line.strip())
            new_indices = [i for i, url in enumerate(urls) if url not in old_urls]
    except FileNotFoundError:
        new_indices = [0]

    updated_urls = urls + urls_p2

    # buffers/last-{dept_id}.txt 파일 갱신
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated_urls))

    return new_indices