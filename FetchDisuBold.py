'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : FetchDisuBold.py
  - Description      : Fetch Disu Major Anouncement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

from autoscraper import AutoScraper
from Update import UpdateFetch,FetchSimilar
import Notify

scraper = AutoScraper()
url='https://www.disu.ac.kr/community/notice?cidx=42&page=1'
dept="차세대반도체학과"
level="주요 공지사항"

if (title:=UpdateFetch('models/disu-title-bold.json',url,'disu-update-bold.txt')):
    date=FetchSimilar("models/disu-date-bold.json",url)[0]
    url=FetchSimilar("models/disu-url-bold.json",url)[0]

    Notify.Email(dept,title,date,level,url)