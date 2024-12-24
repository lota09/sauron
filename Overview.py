'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : FetchTitle.py
  - Description      : Fetch Title from url - each has unique fetch model
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.23 : Initial release
*******************************************************************'''
# -*- coding: utf-8 -*-

from autoscraper import AutoScraper
from Update import *
from Errors import *

MAX_PAGES = 5

USAINT_URL='https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&keyword'
ECO_URL='https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page='
DISU_URL = 'https://www.disu.ac.kr/community/notice?cidx=42&page='
BUFFER_FILES={
    "usaint": "buffers/usaint-update.txt",
    "disu": "buffers/disu-update.txt",
    "disu_bold": "buffers/disu-update-bold.txt",
    "eco": "buffers/eco-update.txt",
    "eco_bold": "buffers/eco-update-bold.txt"
}

scraper = AutoScraper()

def FetchSimilar(model,url):
    scraper.load(model)
    return scraper.get_result_similar(url)


def UpdateUsaint():
    MODEL_TITLE= "models/usaint-title.json"
    MODEL_DATE= "models/usaint-date.json"
    MODEL_URL= "models/usaint-url.json"
    buffer_file= BUFFER_FILES['usaint']
    
    new_title= FetchSimilar(MODEL_TITLE,USAINT_URL)[0]
    
    if CheckLatest(new_title,buffer_file) is False:
        return

    overview={
        'dept': '유세인트',
        'title': new_title,
        'date': FetchSimilar(MODEL_DATE,USAINT_URL)[0],
        'level': '주요 공지사항',
        'url': FetchSimilar(MODEL_URL,USAINT_URL)[0],
    }

    return overview


def UpdateDisuBold():
    MODEL_TITLE= "models/disu-title-bold.json"
    MODEL_DATE= "models/disu-date-bold.json"
    MODEL_URL= "models/disu-url-bold.json"
    buffer_file= BUFFER_FILES['disu_bold']
    
    new_title= FetchSimilar(MODEL_TITLE,f"{DISU_URL}1")[0]
    
    if CheckLatest(new_title,buffer_file) is False:
        return

    overview={
        'dept': '차세대반도체학과',
        'title': new_title,
        'date': FetchSimilar(MODEL_DATE,f"{DISU_URL}1")[0],
        'level': '주요 공지사항',
        'url': FetchSimilar(MODEL_URL,f"{DISU_URL}1")[0],
    }

    return overview


def UpdateEcoBold():
    MODEL_TITLE= "models/eco-title-bold.json"
    MODEL_DATE= "models/eco-date.json"
    MODEL_URL= "models/eco-url.json"
    buffer_file= BUFFER_FILES['eco_bold']
    
    new_title= FetchSimilar(MODEL_TITLE,f"{ECO_URL}1")[0]
    
    if CheckLatest(new_title,buffer_file) is False:
        return

    overview={
        'dept': '경제학과',
        'title': new_title,
        'date': FetchSimilar(MODEL_DATE,f"{ECO_URL}1")[0],
        'level': '주요 공지사항',
        'url': FetchSimilar(MODEL_URL,f"{ECO_URL}1")[0],
    }

    return overview


def UpdateDisu():
    MODEL_TITLE="models/disu-title.json"
    MODEL_DATE="models/disu-date.json"
    MODEL_URL="models/disu-url.json"
    buffer_file=BUFFER_FILES['disu']

    # 최대 5번까지 시도
    for page in range (1,MAX_PAGES+1):
        try:
            new_title = FetchSimilar(MODEL_TITLE, f"{DISU_URL}{page}")[0]
            break
        except IndexError:
            continue
        except Exception as e:
            raise FetchError() from e
    else:
        raise FetchError("Fetch Failed After 5 Pages.")
    
    if CheckLatest(new_title,buffer_file) is False:
        return
        
    overview={
        'dept': '차세대반도체학과',
        'title': new_title,
        'date': FetchSimilar(MODEL_DATE, f"{DISU_URL}{page}")[0],
        'level': '일반 공지사항',
        'url': FetchSimilar(MODEL_URL, f"{DISU_URL}{page}")[0],
    }
    
    return overview


def UpdateEco():
    MODEL_TITLE_ALL= "models/eco-title.json"
    MODEL_TITLE_BOLD= "models/eco-title-bold.json"
    MODEL_DATE= "models/eco-date.json"
    MODEL_URL= "models/eco-url.json"
    buffer_file=BUFFER_FILES['eco']

    # 일반 공지사항 위치 찾기 - 최대 5번까지 시도
    for page in range (1,MAX_PAGES+1):
        titles_all=FetchSimilar(MODEL_TITLE_ALL,f"{ECO_URL}{page}")
        titles_bold=FetchSimilar(MODEL_TITLE_BOLD,f"{ECO_URL}{page}")
        
        if len(titles_all)>len(titles_bold):
            pivot=len(titles_bold)
            break
    else:
        raise FetchError("Fetch Failed, Major Announcements are everywhere.")
        
    new_title= titles_all[pivot]
    
    if CheckLatest(new_title,buffer_file) is False:
        return
        
    overview={
        'dept': '경제학과',
        'title': new_title,
        'date': FetchSimilar(MODEL_DATE,f"{ECO_URL}{page}")[pivot],
        'level': '일반 공지사항',
        'url': FetchSimilar(MODEL_URL,f"{ECO_URL}{page}")[pivot],
    }
    
    return overview





if __name__ == '__main__':
    result=UpdateUsaint()
    print(result)
    if result:
        UpdateLatest(result['title'],BUFFER_FILES['usaint'])
