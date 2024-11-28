'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : KakaoWhoami.py
  - Description      : Get Self info in order to prolong Kakao authorization
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.26 : Initial release
*******************************************************************'''

import json
import requests
from Errors import *

# kakao-api-info.json 파일 경로
API_INFO_PATH = 'kakao-api-info.json'

# 카카오 URL
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_API_URL = "https://kapi.kakao.com/v2/user/me"

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
    refresh_token = api_info['REFRESH_TOKEN']
    
    payload = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token,
    }

    response = requests.post(TOKEN_URL, data=payload)

    if response.status_code == 200:
        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise ValueError("ACCESS_TOKEN 갱신 실패.")
        return access_token
    else:
        raise ValueError(f"ACCESS_TOKEN 발급 실패: {response.status_code}, {response.text}")

def Whoami():
    """
    카카오톡 친구 목록을 얻는 함수. 필요시 ACCESS_TOKEN을 갱신.
    """
    # 1. Access Token 발급 또는 갱신
    access_token = get_access_token()

    # 2. 요청 파라미터 구성

    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Authorization": f"Bearer {access_token}"
    }

    # 3. 사용자 정보 요청
    response = requests.post(KAKAO_API_URL, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise KakaoTalkError(f"사용자 정보 가져오기 실패 : {response.text}")


# 테스트
if __name__ == "__main__":
    try:
        result = Whoami()
        print(result)
    except KakaoTalkError as e:
        print(e)
    except Exception as e:
        print(f"알 수 없는 오류 발생: {e}")
        
#curl -v -G GET "https://kauth.kakao.com/oauth/logout?client_id=85b5de846cdb891ea74a21fae299e6b9&logout_redirect_uri=http://localhost:8080"