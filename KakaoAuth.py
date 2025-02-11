'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : KakaoTalk.py
  - Description      : Process Kakao APIs
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.26 : Initial release
*******************************************************************'''

import json
import requests
from Errors import KakaoTalkError

# kakao-api-info.json 파일 경로
API_INFO_PATH = 'api_infos/kakao-api-info.json'

# 카카오 URL
TOKEN_URL = "https://kauth.kakao.com/oauth/token"

def get_api_info():
    """ kakao-api-info.json 파일에서 REST_API_KEY 정보를 읽어옴 """
    with open(API_INFO_PATH, 'r', encoding='utf-8') as file:
        return json.load(file)

def get_access_token():
    """
    refresh_token 으로 Access Token을 새로 발급
    """
    api_info = get_api_info()
    rest_api_key = api_info['REST_API_KEY']
    REFRESH_TOKEN=api_info['REFRESH_TOKEN']
    
    payload = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": REFRESH_TOKEN,
    }

    response = requests.post(TOKEN_URL, data=payload)

    if response.status_code == 200:
        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise KakaoTalkError("ACCESS_TOKEN 갱신 실패.")
        return access_token
    else:
        raise KakaoTalkError(f"ACCESS_TOKEN 발급 실패: {response.status_code}, {response.text}")