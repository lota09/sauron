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

scraper = AutoScraper()
usaint_url='https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&keyword'
eco_url='https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&page='
disu_url = 'https://www.disu.ac.kr/community/notice?cidx=42&page='

    
#Notify.Email(dept,title,date,level,usaint_url)
        
def UpdateUsaint():

    result={
        'dept': '유세인트',
        'title': UpdateFetch('models/usaint-title.json',usaint_url,'usaint-update.txt'),
        'date': None,
        'level': '주요 공지사항',
        'url': None,
    }

    if (result['title']):
        result['date']=FetchSimilar("models/usaint-date.json",usaint_url)[0]
        result['url']=FetchSimilar("models/usaint-url.json",usaint_url)[0]
        
        return result
    
    return


def UpdateEcoBold():
    
    result={
        'dept': '경제학과',
        'title': UpdateFetch('models/eco-title-bold.json',f"{eco_url}1",'eco-update-bold.txt'),
        'date': None,
        'level': '주요 공지사항',
        'url': None,
    }

    if (result['title']):
        result['date']=FetchSimilar("models/eco-date.json",f"{eco_url}1")[0]
        result['url']=FetchSimilar("models/eco-url.json",f"{eco_url}1")[0]
        
        return result
    
    return


def UpdateEco():
    page=1  
    result={
        'dept': '경제학과',
        'title': None,
        'date': None,
        'level': '일반 공지사항',
        'url': None,
    }

    # 일반 공지사항 위치 찾기 - 최대 5번까지 시도
    while page <5:
        alltitle=FetchSimilar("models/eco-title.json",f"{eco_url}{page}")
        boldtitle=FetchSimilar("models/eco-title-bold.json",f"{eco_url}{page}")
        
        if len(alltitle)>len(boldtitle):
            minoridx=len(boldtitle)
            break
        
        if (page:= page+1) >= 5:
            raise FetchError("Fetch Failed Major Announcements are everywhere.")

    result['title']= ManualUpdate(alltitle[minoridx],'eco-update.txt')
    
    if (result['title']):
        result['date']=FetchSimilar("models/eco-date.json",f"{eco_url}{page}")[minoridx]
        result['url']=FetchSimilar("models/eco-url.json",f"{eco_url}{page}")[minoridx]
        
        return result
    
    return


def UpdateDisu():
    page=1  
    result={
        'dept': '차세대반도체학과',
        'title':None,
        'date': None,
        'level': '일반 공지사항',
        'url': None,
    }

    # 최대 5번까지 시도
    while page < 5:
        try:
            result['title'] = UpdateFetch('models/disu-title.json', f"{disu_url}{page}", 'disu-update.txt')
            break
        except IndexError:
            if (page:= page+1) >= 5:
                raise FetchError("Fetch Failed After 5 Pages.")
        except:
            raise FetchError()
            
    # 새로운 공지일때
    if result['title']:
        result['date'] = FetchSimilar("models/disu-date.json", f"{disu_url}{page}")[0]
        result['url'] = FetchSimilar("models/disu-url.json", f"{disu_url}{page}")[0]
        
        return result
    
    return


def UpdateDisuBold():
    
    result={
        'dept': '차세대반도체학과',
        'title': UpdateFetch('models/disu-title-bold.json',f"{disu_url}1",'disu-update-bold.txt'),
        'date': None,
        'level': '주요 공지사항',
        'url': None,
    }

    if (result['title']):
        result['date']=FetchSimilar("models/disu-date-bold.json",f"{disu_url}1")[0]
        result['url']=FetchSimilar("models/disu-url-bold.json",f"{disu_url}1")[0]
        
        return result
    
    return
        

if __name__ == '__main__':
    result=UpdateDisuBold()
    print(result)
