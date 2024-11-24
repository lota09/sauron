import json
import requests
from Errors import *

# kakao-api-info.json 파일 경로
API_INFO_PATH = 'kakao-api-info.json'

# 카카오 URL
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_API_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

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
    try:
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
        response = requests.post(KAKAO_API_URL, headers=headers, data=payload)

        if response.status_code == 200:
            return response.json()
        else:
            raise KakaoTalkError("메시지 전송 실패:", response.status_code, response.text)

    except Exception as e:
        raise KakaoTalkError



# 테스트
if __name__ == "__main__":
    message = f"안녕하세요! 카카오 API를 통해 전송된 메시지입니다."
    SendSelfMessage("https://scatch.ssu.ac.kr/%ea%b3%b5%ec%a7%80%ec%82%ac%ed%95%ad/?f&category=%ED%95%99%EC%82%AC&slug=%EA%B5%90%EC%96%91%EA%B5%90%EC%9C%A1%EC%97%B0%EA%B5%AC%EC%84%BC%ED%84%B0-2024-2%ED%95%99%EA%B8%B0-%EA%B5%90%EC%96%91%EA%B5%90%EC%9C%A1-%ED%98%81%EC%8B%A0%EC%88%98%EC%97%85%EB%AA%A8%ED%98%95engaged-4&keyword")