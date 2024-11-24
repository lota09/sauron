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
    
    if (components := func()) is None:
        continue
    
    content= fetch_content[idx](components['url'])
    components['summary']= ''
    
    if (content):
        components['summary']= ClovaSummary.Summarize(f"제목:{components['title']}\n내용:\n{content}")
    
    Notify.Email(components)
    KakaoTalk.SendSelfMessage(components)
    