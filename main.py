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

fetch_overview=[
    Overview.UpdateUsaint,
    Overview.UpdateDisu,
    Overview.UpdateDisuBold,
    Overview.UpdateEco,
    Overview.UpdateEcoBold
]

fetch_content=[
    Content.FetchUsaint,
    Content.FetchDisu,
    Content.FetchDisu,
    Content.FetchEco,
    Content.FetchEco,
]

for idx, func in enumerate(fetch_overview):
    
    if (overview := func()) is None:
        continue
    
    dept= overview['dept']
    title= overview['title']
    date= overview['date']
    level= overview['level']
    url= overview['url']
    content= fetch_content[idx](url)
    summary= ''
    
    if (content):
        summary= ClovaSummary.Summarize(f"제목:{title}\n내용:\n{content}")
    
    # 덮어쓸 텍스트 정의
    email_content = f"""\
To: tjrals120@gmail.com
From: tjrals120@gmail.com
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8
Subject: [{dept}]{title}

<html>
    <body>
        <h2>{dept} {level}</h2>
        {date}
        <hr>
        {summary.replace("\n", "<br>")}
        <hr>
        <h5><a href="{url}">{title}</a></h5>
    </body>
</html>
"""
    
    Notify.Email(email_content)
    
        
        
        
        






