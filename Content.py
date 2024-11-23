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
from Errors import *


def FetchUsaint(url):
    # 1. URL에서 HTML 가져오기
    response = requests.get(url)
    if response.status_code != 200:
        raise FetchError(f"Failed to fetch the page. Status code: {response.status_code}")

    # 2. HTML 파싱
    soup = BeautifulSoup(response.text, 'html.parser')

    # 3. 대상 div 탐색
    container = soup.find('div', class_='bg-white p-4 mb-5')
    if not container:
        raise FetchError("본문 내용이 포함된 컨테이너를 찾을 수 없습니다.")

    # 4. 제목 추출
    #title = container.find('h2').get_text(strip=True)

    # 5. 본문 내용 추출
    paragraphs = container.find_all('p')
    content = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

    # 6. 결과 반환
    return content


def FetchEco(url):
    # 1. URL에서 HTML 가져오기
    response = requests.get(url)
    if response.status_code != 200:
        raise FetchError(f"페이지를 불러오는 데 실패했습니다. 상태 코드: {response.status_code}")
    
    # 2. HTML 파싱
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 3. 제목 추출
    #title_section = soup.find('h2', id='bo_v_title')
    #title = title_section.find('span', class_='bo_v_tit').get_text(strip=True)
    
    # 4. 본문 내용 추출
    content_div = soup.find('div', id='bo_v_con')
    paragraphs = content_div.find_all('p')
    content = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

    # 5. 결과 반환
    return content


def FetchDisu(url):
    # 1. URL에서 HTML 가져오기
    response = requests.get(url)
    if response.status_code != 200:
        raise FetchError(f"페이지를 불러오는 데 실패했습니다. 상태 코드: {response.status_code}")
    
    # 2. HTML 파싱
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 2. 제목 추출
    #title = soup.find('h1', class_='bbstitle').get_text(strip=True)
    
    # 4. 본문 내용 추출
    content_div = soup.find('div', class_='bbs_contents')
    content_lines = content_div.find_all(text=True, recursive=True)
    content = "\n".join(line.strip() for line in content_lines if line.strip())

    # 5. 결과 반환
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