'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : FetchUsaint.py
  - Description      : Fetch Usaint Anouncement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

from autoscraper import AutoScraper
from Update import UpdateFetch,FetchSimilar
import Notify

scraper = AutoScraper()
url='https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&keyword'
profile="유세인트"

if (title:=UpdateFetch('models/usaint-title.json',url,'usaint-update.txt')):
    date=FetchSimilar("models/usaint-date.json",url)[0]
    url=FetchSimilar("models/usaint-url.json",url)[0]

    Notify.Email(profile,title,date,url)