'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : KakaoFriend.py
  - Description      : Get Kakao Friends teammates
  - Owner            : Seokmin.Kang
  - Revision history : 1) 2024.11.26 : Initial release
*******************************************************************'''

import requests

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from KakaoAuth import *
from Errors import KakaoTalkError


# 카카오 URL
KAKAO_API_URL_WHOAMI = "https://kapi.kakao.com/v2/user/me"
KAKAO_API_URL_GETFRIENDS = "https://kapi.kakao.com/v1/api/talk/friends"
KAKAO_API_URL_LOGOUT = "https://kauth.kakao.com/oauth/logout"


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
    response = requests.post(KAKAO_API_URL_WHOAMI, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise KakaoTalkError(f"사용자 정보 가져오기 실패 : {response.text}")


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
    response = requests.get(KAKAO_API_URL_GETFRIENDS, headers=headers, params=payload)
    
    if response.status_code == 200:
        data = response.json()
        #data : {'elements': [], 'total_count': 0, 'after_url': None, 'favorite_count': 0}
        if 'elements' in data:
            return data['elements']
        else:
            raise KakaoTalkError("응답에 친구 목록이 없습니다.")
    else:
        raise KakaoTalkError(f"친구목록 요청실패 : {response.text}")
    
    
def Logout():
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

    # 3. 로그아웃 요청
    response = requests.post(KAKAO_API_URL_LOGOUT, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise KakaoTalkError(f"친구목록 요청실패 : {response.text}")



# 테스트
if __name__ == "__main__":
    try:
        #result = GetFriends(10, 0)
        result = Whoami()

        print(result)
    except KakaoTalkError as e:
        print(e)
    except Exception as e:
        print(f"알 수 없는 오류 발생: {e}")
