'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : FetchDisuBold.py
  - Description      : Fetch Disu Major Announcement
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.21 : Initial release
*******************************************************************'''

from autoscraper import AutoScraper
from Update import UpdateFetch, FetchSimilar
import Notify
from Errors import *

scraper = AutoScraper()
url = 'https://www.disu.ac.kr/community/notice?cidx=42&page='
page = 1
profile = "차세대반도체학과"

# 최대 5번까지 시도
while page < 5:
    try:
        title = UpdateFetch('models/disu-title.json', f"{url}{page}", 'disu-update.txt')
        break
    except IndexError:
        if (page:= page+1) >= 5:
            raise FetchError("Fetch Failed After 5 Pages.")
    except:
        raise FetchError()
        
# 새로운 공지일때
if title:
    date = FetchSimilar("models/disu-date.json", f"{url}{page}")[0]
    url = FetchSimilar("models/disu-url.json", f"{url}{page}")[0]

    Notify.Email(profile, title, date, url)
