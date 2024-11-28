'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : KakaoFriend.py
  - Description      : Get Kakao Friends teammates
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
KAKAO_API_URL = "https://kapi.kakao.com/v1/api/talk/friends"

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

def GetFriends(limit, page):
    """
    카카오톡 친구 목록을 얻는 함수. 필요시 ACCESS_TOKEN을 갱신.
    """
    # 1. Access Token 발급 또는 갱신
    access_token = get_access_token()

    # 2. 요청 파라미터 구성
    payload = {
        "limit": limit,
        "offset": page * limit,
        "friend_order": "favorite"
    }

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    # 3. 친구 목록 요청
    response = requests.get(KAKAO_API_URL, headers=headers, params=payload)
    
    if response.status_code == 200:
        data = response.json()
        #data : {'elements': [], 'total_count': 0, 'after_url': None, 'favorite_count': 0}
        if 'elements' in data:
            return data['elements']
        else:
            raise KakaoTalkError("응답에 친구 목록이 없습니다.")
    else:
        raise KakaoTalkError(f"친구목록 요청실패 : {response.text}")


# 테스트
if __name__ == "__main__":
    try:
        result = GetFriends(10, 0)
        print(result)
    except KakaoTalkError as e:
        print(e)
    except Exception as e:
        print(f"알 수 없는 오류 발생: {e}")
