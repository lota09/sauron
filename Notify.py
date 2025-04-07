'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : Notify.py
  - Description      : Notify fetched announcement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''
# -*- coding: utf-8 -*-

import os
import subprocess

EMAIL_FILE = "buffers/email.txt"

def Email(components):
    
    dept= components['dept']
    title= components['title']
    date= components['date']
    url= components['url']
    summary= components['summary']
    
    email_content = f"""\
To: tjrals120@gmail.com
From: tjrals120@gmail.com
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8
Subject: [{dept}]{title}

<html>
    <body>
        <h2>{dept}</h2>
        {date}
        <hr>
        {summary.replace("\n", "<br>")}
        <hr>
        <h5><a href="{url}">{title}</a></h5>
    </body>
</html>
"""

    # 파일에 텍스트 덮어쓰기
    
    #부모 디렉터리 없으면 생성
    os.makedirs(os.path.dirname(EMAIL_FILE), exist_ok=True)
    
    with open(EMAIL_FILE, "w", encoding="utf-8") as file:
        file.write(email_content)

    try:
        subprocess.run(
        f"cat {EMAIL_FILE} | ssmtp -v -t",
        shell=True,  # 파이프를 처리하기 위해 shell=True 사용
        text=True,
        stdout=subprocess.PIPE,  # 표준 출력 캡처
        stderr=subprocess.PIPE,  # 표준 에러 캡처
        check=True,  # 명령이 실패하면 예외 발생
        )
        # 명령 성공 시 동작
        return 0

    except subprocess.CalledProcessError:
        # 명령 실패 시 동작
        return 1