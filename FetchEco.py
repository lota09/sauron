'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : FetchEco.py
  - Description      : Fetch Economics Anouncement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

from autoscraper import AutoScraper
from Update import *
import Notify
from Errors import *

scraper = AutoScraper()
url='https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page='
page=1
dept="경제학과"
level="일반 공지사항"

# 일반 공지사항 위치 찾기 - 최대 5번까지 시도
while page <5:
    alltitle=FetchSimilar("models/eco-title.json",f"{url}{page}")
    boldtitle=FetchSimilar("models/eco-title-bold.json",f"{url}{page}")
    
    if len(alltitle)>len(boldtitle):
        minoridx=len(boldtitle)
        break
    
    if (page:= page+1) >= 5:
        raise FetchError("Fetch Failed Major Announcements are everywhere.")


if (title:=ManualUpdate(alltitle[minoridx],'eco-update.txt')):
    date=FetchSimilar("models/eco-date.json",f"{url}{page}")[minoridx]
    url=FetchSimilar("models/eco-url.json",f"{url}{page}")[minoridx]

    Notify.Email(dept,title,date,level,url)
