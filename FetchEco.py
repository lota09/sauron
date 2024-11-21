from autoscraper import AutoScraper
from Update import UpdateFetch,FetchSimilar
import Notify

scraper = AutoScraper()
url='https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page=1'
profile="경제학과"

if (title:=UpdateFetch('models/eco-title-all.json',url,'eco-update.txt')):
    date=FetchSimilar("models/eco-date.json",url)[0]
    url=FetchSimilar("models/eco-url.json",url)[0]

    Notify.Email(profile,title,date,url)