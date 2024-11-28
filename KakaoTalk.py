'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : KakaoTalk.py
  - Description      : Process Kakao APIs
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
KAKAO_API_URL_SELF = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
KAKAO_API_URL_FRIEND = "https://kapi.kakao.com/v1/api/talk/friends/message/default/send"

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
            raise ValueError("ACCESS_TOKEN 갱신 실패.")
        return access_token
    else:
        raise ValueError(f"ACCESS_TOKEN 발급 실패: {response.status_code}, {response.text}")

def SendSelfMessage(components):
    """
    카카오톡 메시지를 보내는 함수. 필요시 ACCESS_TOKEN을 갱신.
    """

    # 1. Access Token 발급 또는 갱신
    access_token = get_access_token()
        
    #메시지 구성요소
    dept= components['dept']
    title= components['title']
    level= components['level']
    url= components['url']
    summary= components['summary']

    # 2. 메시지 템플릿 구성
    payload = \
        {
            "template_object": json.dumps(
                {
                    "object_type": "feed",
                    "content": {
                        "title": title,
                        "description": summary,
                        "link": {
                            "web_url": url,
                            "mobile_web_url": url,
                        }
                    },
                    "item_content": { 
                        "profile_text": f"{dept} {level}",
                        "profile_image_url": "https://ssu.ac.kr/wp-content/uploads/2019/05/suu_emblem1.jpg",
                    }
                }
            )
        }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"
    }

    # 3. 메시지 전송 요청
    response = requests.post(KAKAO_API_URL_SELF, headers=headers, data=payload)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise KakaoTalkError(f"메시지 전송 실패: {response.text}")


def SendFriendMessage(components,receiver_uuids):
    """
    카카오톡 메시지를 보내는 함수. 필요시 ACCESS_TOKEN을 갱신.
    """

    # 1. Access Token 발급 또는 갱신
    access_token = get_access_token()
        
    #메시지 구성요소
    dept= components['dept']
    title= components['title']
    level= components['level']
    url= components['url']
    summary= components['summary']

    # 2. 메시지 템플릿 구성
    payload = \
        {
            "receiver_uuids": json.dumps(receiver_uuids),
            "template_object": json.dumps(
                {
                    "object_type": "feed",
                    "content": {
                        "title": title,
                        "description": summary,
                        "link": {
                            "web_url": url,
                            "mobile_web_url": url,
                        }
                    },
                    "item_content": { 
                        "profile_text": f"{dept} {level}",
                        "profile_image_url": "https://ssu.ac.kr/wp-content/uploads/2019/05/suu_emblem1.jpg",
                    }
                }
            )
        }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"
    }

    # 3. 메시지 전송 요청
    response = requests.post(KAKAO_API_URL_FRIEND, headers=headers, data=payload)
    
    if response.status_code == 200:
        return response.json()
    else:
        raise KakaoTalkError(f"메시지 전송 실패: {response.text}")


# 테스트
if __name__ == "__main__":
    receiver_uuids=["jL-Iu4K3hbeEqJqomaiZr5iulrqLuoy-jrqD7A"]
    components = \
        {
            'dept': '차세대반도체학과',
            'title': '차세대반도체학과 반도체 세미나 I Advanced Package 이해 I 2024. 11. 20. (수) I 반도체산업이해(특강)',
            'date': '2024-11-18', 'level': '일반 공지사항', 'url': 'https://www.disu.ac.kr/community/notice?md=v&bbsidx=7978',
            'summary': '- 숭실대학교 차세대반도체학과에서 반도체 산업이해 오픈 특강을 진행함\n- 반도체 산업에서 패키지의 중요성에 대해 이해하는 시간이 되기를 바람'
        }
    #result=SendSelfMessage(components)
    result=SendFriendMessage(components,receiver_uuids)
    
    print(result)
    