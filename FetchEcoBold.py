'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : FetchEco.py
  - Description      : Fetch Economics Anouncement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

from autoscraper import AutoScraper
from Update import UpdateFetch,FetchSimilar
import Notify

scraper = AutoScraper()
url='https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page=1'
dept="경제학과"
level="주요 공지사항"

if (title:=UpdateFetch('models/eco-title.json',url,'eco-update-bold.txt')):
    date=FetchSimilar("models/eco-date.json",url)[0]
    url=FetchSimilar("models/eco-url.json",url)[0]

    Notify.Email(dept,title,date,level,url)