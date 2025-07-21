'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : FetchTitle.py
  - Description      : Fetch Title from url - each has unique fetch model
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.23 : Initial release
*******************************************************************'''
# -*- coding: utf-8 -*-

import unicodedata
from autoscraper import AutoScraper
import Update
import Content
import ClovaSummary
from Errors import FetchError, SummaryError

MAX_PAGES = 5

try:
    import Hyperparms
    SUMMARY_EN = Hyperparms.SUMMARY_EN
except:
    SUMMARY_EN = True

#모델, 버퍼파일 이름 재설정

scraper = AutoScraper()

def FetchSimilar(model,url):
    scraper.load(model)
    return scraper.get_result_similar(url)


def isDemoted(dept):
    dept_id = dept.dept_id

    # BOLD 구분 유형에 따른 Demotion 함수 결정
    if "binary" in dept.misc:
        demotion_func = FetchNotbold
    elif "even" in dept.misc:
        demotion_func = FetchAll

    #Demotion 개념이 없는 부서일 경우
    else:
        return False

    titles = demotion_func(dept)
    #종료된 공지사항에서 기록된 마지막 공지를 찾음
    return Update.IndexPrevious(titles,dept_id) is not None


def MakePointer(last_idx,pivot=0):
    # 최신항목이 기존 항목과 같은경우
    if (last_idx == pivot):
        return
    # 버퍼 파일이나 기존 항목이 없는경우
    elif (last_idx is None):
        new_idx = pivot
    # pivot < last_idx 인 경우
    elif (pivot < last_idx):
        new_idx = last_idx -1
    # 발생할 수 없는 시나리오 (pivot > last_idx 인 경우)
    else :
        raise IndexError(f"last_idx Cannot Have Value of {last_idx}.")
    
    return new_idx


def UpdateFetch(dept):
    dept_id = dept.dept_id
    url = dept.url

    if "nonbin" in dept.misc:
        pivot = len(FetchAixbold(dept))
    else:
        pivot = 0

    model_title= f"models/title-{dept_id}.json"
    model_url= f"models/url-{dept_id}.json"
    
    titles= FetchSimilar(model_title,url)
    last_idx = Update.IndexPrevious(titles,dept_id)

    new_idx = MakePointer(last_idx,pivot)
    #마지막 공지가 최신공지인 경우
    if new_idx is None:
        return
    
    #마지막 공지사항이 종료된 공지사항이면 갱신함
    if isDemoted(dept):
        Update.UpdateLatest(titles[new_idx],dept_id)
        return

    content_url = FetchSimilar(model_url,url)[new_idx]

    overview={
        'dept': dept,
        'title': unicodedata.normalize('NFC', titles[new_idx]),
        'url': content_url,
        'summary' : '',
        'latest': titles[pivot] == titles[new_idx]
    }

    #공지 내용 가져오기
    content = Content.FetchContent(dept.div_args,content_url)
    
    #클로바 요약
    if (SUMMARY_EN and content):
        try:
            overview['summary']= ClovaSummary.Summarize(f"제목:{overview['title']}\n내용:\n{content}")
        except SummaryError as e:
            overview['summary']= "요약을 실패하였습니다"

    return overview

# bold - notbold 두가지 형태로 구분할수 있는경우
def FetchNotbold(dept):

    url = dept.url[:-1]
    dept_id = dept.dept_id

    # 예를들어, disu_bold 에서 앞부분 disu만 가져옴
    dept_id_normal = dept_id.split('_')[0]

    # 최대 5번까지 시도
    for page in range (1,MAX_PAGES+1):
        try:
            titles = FetchSimilar(f"models/title-{dept_id_normal}.json", f"{url}{page}")
            if not titles:
                continue
            break
        except Exception as e:
            raise FetchError(e) from e
    else:
        raise FetchError("Fetch Failed After 5 Pages.")
    
    return titles

# ("even" 처럼) 같은 이름이 포함된 HTML속성때문에 bold - all 두가지 모델만 있는경우
def FetchAll(dept):

    url = dept.url[:-1]
    dept_id = dept.dept_id

    # 예를들어, disu_bold 에서 앞부분 disu만 가져옴
    dept_id_all = dept_id.split('_')[0]

    # 일반 공지사항 위치 찾기 - 최대 5번까지 시도
    for page in range (1,MAX_PAGES+1):
        titles_all=FetchSimilar(f"models/title-{dept_id_all}.json",f"{url}{page}")
        titles_bold=FetchSimilar(f"models/title-{dept_id_all}_bold.json",f"{url}{page}")
        
        if len(titles_all)>len(titles_bold):
            break
    else:
        raise FetchError("Fetch Failed, Major Announcements are everywhere.")
    
    #차집합 titles = titles_all - titles_bold
    titles = [item for item in titles_all if item not in titles_bold]

    return titles

# 속성 이름에 의한 구분이 어려운경우
def FetchAixbold(dept):

    titles_all=FetchSimilar(f"models/title-{dept.dept_id}.json",dept.url)
    titles_normalized = [unicodedata.normalize('NFC', title) for title in titles_all]
    filtered = [item for item in titles_normalized if '[공지]' in item]

    return filtered


if __name__ == '__main__':
    #result = UpdateFetch("disu_bold",summary=False)
    result = UpdateFetch("aix_nonbin")
    print(result)
