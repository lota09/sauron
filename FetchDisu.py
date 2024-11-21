from autoscraper import AutoScraper
from Update import UpdateFetch,FetchSimilar
import Notify

scraper = AutoScraper()
url='https://www.disu.ac.kr/community/notice?cidx=42&page=1'
profile="차세대반도체학과"

if (title:=UpdateFetch('models/disu-title.json',url,'disu-update.txt')):
    date=FetchSimilar("models/disu-date.json",url)[0]
    url=FetchSimilar("models/disu-url.json",url)[0]

    Notify.Email(profile,title,date,url)