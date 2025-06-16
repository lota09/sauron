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
DIV_ARGS={
    "usaint": {'class_':'bg-white p-4 mb-5'},
    "disu_bold": {'class_':'bbs_contents'},
    "eco_bold": {'id':'bo_v_con'},
    "cse_bold": {'id':'bo_v_con'},
    "aix_nonbin":{"class":"table-responsive"},
    "disu_polaris": {'class_':'bbs_contents'}
}

def FetchContent(dept_id,url):

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