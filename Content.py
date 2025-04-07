'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : Content.py
  - Description      : Fetch Contents from url - each has unique fetch model
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.22 : Initial release
*******************************************************************'''

# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
from Errors import FetchError

def FetchContent(dept_id,url):
    DIV_ARGS={
        "usaint": {'class_':'bg-white p-4 mb-5'},
        "disu_bold": {'class_':'bbs_contents'},
        "eco_bold": {'id':'bo_v_con'},
        "cse_bold": {'id':'bo_v_con'}
    }

    #Html div 키워드가 지정되지 않은경우
    if DIV_ARGS.get(dept_id,None) is None:
        return
    
    # 1. HTML 요청
    response = requests.get(url)
    if response.status_code != 200:
        raise FetchError(f"페이지를 불러오는 데 실패했습니다. 상태 코드: {response.status_code}")
    
    # 2. HTML 파싱
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 3. 본문 추출
    content_div = soup.find('div', **DIV_ARGS[dept_id])
    if not content_div:
        raise FetchError("본문을 찾을 수 없습니다.")
    
    # 4. 모든 텍스트를 평탄하게 추출
    content = "\n".join(content_div.stripped_strings)

    # 5. 반환
    return content


'''
# Example usage:
url = "https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&slug=2025-1%ED%95%99%EA%B8%B0-%EA%B5%AD%EC%99%B8%ED%98%B8%EC%A3%BC-%ED%98%84%EC%9E%A5%EC%8B%A4%EC%8A%B5%ED%95%99%EA%B8%B0%EC%A0%9C-%EC%84%A4%EB%AA%85%ED%9A%8C-%EA%B0%9C%EC%B5%9C-%EC%95%88%EB%82%B4&keyword"
try:
    title, content = FetchUsaintContents(url)
    print(f"---제목---\n {title}\n")
    print(f"---본문 내용---\n{content}")
except Exception as e:
    print(f"에러 발생: {e}")
'''