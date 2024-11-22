'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : Notify.py
  - Description      : Notify fetched announcement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

import subprocess

def Email(dept,title,date,level,url):
    # 덮어쓸 텍스트 정의
    email_content = f"""To: tjrals120@gmail.com
From: tjrals120@gmail.com
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8
Subject: [{dept}]{title}

<html>
    <body>
        <h3>{dept} {level}</h3>
        <hr>
        <a href="{url}">{title}</a>
        <p>{date}</p>
    </body>
</html>
"""

    # 파일에 텍스트 덮어쓰기
    with open("email.txt", "w", encoding="utf-8") as file:
        file.write(email_content)

    try:
        result = subprocess.run(
            "cat email.txt | ssmtp -v -t",
            shell=True,  # 파이프를 처리하기 위해 shell=True 사용
            text=True,
            stdout=subprocess.PIPE,  # 표준 출력 캡처
            stderr=subprocess.PIPE,  # 표준 에러 캡처
            check=True,  # 명령이 실패하면 예외 발생
        )
        # 명령 성공 시 동작
        return 0

    except subprocess.CalledProcessError as e:
        # 명령 실패 시 동작
        return 1