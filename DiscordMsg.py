'''*******************************************************************
  - Project          : The Eye Of Sauron
  - File name        : DiscordMsg.py
  - Description      : Transfer Notice to send Discord msg
  - Owner            : Seokmin.Kang
  - Revision history :  1) 2025.04.x  : Initial release
                        2) 2025.04.12 : Updated footer Icon display feature
*******************************************************************'''

import requests
import json
import os
from datetime import datetime, timezone
from Errors import DiscordError

# 디스코드 설정
BOT_TOKEN_FILE = 'secrets/discord-api-info.json'
CHANNEL_ID_DEBUG = "1355610759777882162"
ICON_DEBUG = "https://cdn.discordapp.com/attachments/1355611235156234473/1360547635672649869/c7dca22d3f65a53a.png?ex=67fb843a&is=67fa32ba&hm=f33da564c49964ab9ef2ce280a651dc9f968926d70be675db62f4fd8af4332fb&"

try:
    import Hyperparms
    DEBUG_EN = Hyperparms.DEBUG_EN
except:
    DEBUG_EN = False

def LoadSecrets(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Configuration file not found: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as file:
        try:
            api_info = json.load(file)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format in {file_path}: {e}")

    required_keys = {'bot_token'}
    if not required_keys.issubset(api_info.keys()):
        raise ValueError(f"Missing required keys in JSON file: {required_keys - api_info.keys()}")

    return api_info


# 내용 메시지 전송 함수
def SendContentMessage(content):

    api_info = LoadSecrets(BOT_TOKEN_FILE)
    bot_token=api_info['bot_token']

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    data = {"content": content}

    discord_api_url = f"https://discord.com/api/v10/channels/{CHANNEL_ID_DEBUG}/messages"
    response = requests.post(discord_api_url, headers=headers, data=json.dumps(data))

    if response.status_code == 200 or response.status_code == 201:
        return response.json()
    else:
        raise DiscordError(f"메시지 전송 실패: {response.status_code}, {response.text}")

# 사용자 지정 임베드 메시지 전송함수
def SendCustomMessage(embed, channel_id):
    #메시지 구성요소

    api_info = LoadSecrets(BOT_TOKEN_FILE)
    bot_token=api_info['bot_token']

    data = {"embeds": [embed]}
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    discord_api_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    response = requests.post(discord_api_url, headers=headers, data=json.dumps(data))
    
    if response.status_code == 200 or response.status_code == 201:
        return response.json()
    else:
        raise DiscordError(f"메시지 전송 실패: {response.status_code}, {response.text}")


# 디버깅 메시지 전송 함수
def SendDebugMessage(content):

    embed = {
        "title": f"⚠️ 디버그 메시지",
        "description": f"\u200b\n{content}",
        "color": 0xe74c3c,  # 빨간색
        "footer": {
            "text": "사우론의 눈" ,
            "icon_url": ICON_DEBUG
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    api_info = LoadSecrets(BOT_TOKEN_FILE)
    bot_token=api_info['bot_token']

    data = {"embeds": [embed]}
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    discord_api_url = f"https://discord.com/api/v10/channels/{CHANNEL_ID_DEBUG}/messages"
    response = requests.post(discord_api_url, headers=headers, data=json.dumps(data))

    if response.status_code == 200 or response.status_code == 201:
        return response.json()
    else:
        raise DiscordError(f"메시지 전송 실패: {response.status_code}, {response.text}")


# 임베드 메시지 전송 함수
def SendEmbedMessage(notice_data):
    #메시지 구성요소
    dept= notice_data.dept
    title= notice_data.title
    url= notice_data.url
    summary= notice_data.summary

    if DEBUG_EN is True:
        channel_id = CHANNEL_ID_DEBUG
        mention = ""
    else:
        channel_id = dept.channel_id
        mention = "@everyone"

    if summary.strip():
        summary = f"\u200b\n{summary}\n\u200b"

    embed = {
        "title": f"📢 {title}",
        "description": summary,
        "color": 0x62c6c4,  # 파란색
        "fields": [
            {"name": "🔗 링크", "value": f"[▶자세히 보기]({url})\n\u200b\n{mention}", "inline": True},
        ],
        "footer": {
            "text": dept.dept_ko ,
            "icon_url": dept.icon_url
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    api_info = LoadSecrets(BOT_TOKEN_FILE)
    bot_token=api_info['bot_token']

    data = {"embeds": [embed]}
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    discord_api_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    response = requests.post(discord_api_url, headers=headers, data=json.dumps(data))
    
    if response.status_code == 200 or response.status_code == 201:
        return response.json()
    else:
        raise DiscordError(f"메시지 전송 실패: {response.status_code}, {response.text}")


# 메시지 전송 실행
if __name__ == "__main__":

    content = \
"""
\u200b
🛠️ 업데이트 내용
- <@&1397154694635327538>의 공지 알림을 이제 받아보실 수 있습니다.
- <@&1398016806802821140>의 공지 알림을 이제 받아보실 수 있습니다.
- 새로운 공지 감지 알고리즘을 개선하였습니다. (기존 : 첫번째 항목 변화감지 → 개선 : 기존 목록과 차집합)
\u200b
💌 추가적인 안내말씀
- 본 서비스에 초대하고 싶으신 분이 있으시다면, 편하게 초대하셔도 좋습니다.
- 사우론의 눈 서비스에 관한 피드백은 상시 받고있으니 <@337458324926627841>에게 편하게 DM을 보내주시기 바랍니다.
\u200b
감사합니다.
\u200b
@everyone
"""
    channel_id = CHANNEL_ID_DEBUG
    embed = {
        "title": f"📢 사우론의 눈 주요 업데이트 노트",
        "description": content,
        "color": 0xfb8229,  # 사우론 오렌지
        "fields": [
            #{"name": "🔗 링크", "value": f"[▶자세히 보기]({url})\n\u200b\n@everyone", "inline": True},
        ],
        "footer": {
            "text": "사우론의 눈" ,
            "icon_url": ICON_DEBUG
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    #SendContentMessage("인간 세계의 끝이 도래했다.")
    #SendDebugMessage(content)
    #SendEmbedMessage(components)
    SendCustomMessage(embed,channel_id)
