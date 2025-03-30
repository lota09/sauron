import requests
import json
import os
from datetime import datetime, timezone
from Errors import DiscordError

# 디스코드 설정
BOT_TOKEN_FILE = 'secrets/discord-api-info.json'
CHANNEL_ID={
    "usaint": "1355604572353069200",
    "disu_bold": "1355609212016918608",
    "eco_bold": "1355609054629593289",
    "debug" : "1355610759777882162"
}

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

    discord_api_url = f"https://discord.com/api/v10/channels/{CHANNEL_ID['debug']}/messages"
    response = requests.post(discord_api_url, headers=headers, data=json.dumps(data))

    if response.status_code == 200 or response.status_code == 201:
        return response.json()
    else:
        raise DiscordError(f"메시지 전송 실패: {response.status_code}, {response.text}")


# 디버깅 메시지 전송 함수
def SendDebugMessage(content):

    embed = {
        "title": f"⚠️ 디버그 메시지",
        "description": f"\u200b\n{content}\n\n@here",
        "color": 0xe74c3c,  # 빨간색
        "footer": {"text": "사우론의 눈"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    api_info = LoadSecrets(BOT_TOKEN_FILE)
    bot_token=api_info['bot_token']

    data = {"embeds": [embed]}
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    discord_api_url = f"https://discord.com/api/v10/channels/{CHANNEL_ID['debug']}/messages"
    response = requests.post(discord_api_url, headers=headers, data=json.dumps(data))

    if response.status_code == 200 or response.status_code == 201:
        return response.json()
    else:
        raise DiscordError(f"메시지 전송 실패: {response.status_code}, {response.text}")


# 임베드 메시지 전송 함수
def SendEmbedMessage(components):

    #메시지 구성요소
    dept= components['dept']
    dept_id= components['dept_id']
    title= components['title']
    url= components['url']
    summary= components['summary']

    embed = {
        "title": f"📢 {title}",
        "description": f"\u200b\n{summary}\n\u200b",
        "color": 0x62c6c4,  # 파란색
        "fields": [
            {"name": "🔗 링크", "value": f"[▶자세히 보기]({url})\n\u200b\n@here", "inline": True},
        ],
        "footer": {"text": dept},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    api_info = LoadSecrets(BOT_TOKEN_FILE)
    bot_token=api_info['bot_token']

    data = {"embeds": [embed]}
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    discord_api_url = f"https://discord.com/api/v10/channels/{CHANNEL_ID[dept_id]}/messages"
    response = requests.post(discord_api_url, headers=headers, data=json.dumps(data))
    
    if response.status_code == 200 or response.status_code == 201:
        return response.json()
    else:
        raise DiscordError(f"메시지 전송 실패: {response.status_code}, {response.text}")


# 메시지 전송 실행
if __name__ == "__main__":
    components = \
        {
            'dept': '차세대반도체학과',
            'dept_id': 'disu_bold',
            'title': '차세대반도체학과 반도체 세미나 I Advanced Package 이해 I 2024. 11. 20. (수) I 반도체산업이해(특강)',
            'date': '2024-11-18', 'level': '일반 공지사항', 'url': 'https://www.disu.ac.kr/community/notice?md=v&bbsidx=7978',
            'summary': '- 숭실대학교 차세대반도체학과에서 반도체 산업이해 오픈 특강을 진행함\n- 반도체 산업에서 패키지의 중요성에 대해 이해하는 시간이 되기를 바람'
        }

    content = \
"""
📜 Module: main.py, line 77
🔧 Function: main
❌ Exception: IndexError - Announcement Still Outdated After 5 Fetchs. 
Outdated Data :{'dept': '경제학과', 'title': '경제학과 성적 우수 백마 장학생 모집', 'level': '주요 공지사항', 'url': 'https://eco.ssu.ac.kr/bbs/board.php?bo_table=notice&wr_id=46&page=1', 'latest': False, 'summary': ''}
"""
    SendContentMessage("인간 세계의 끝이 도래했다.")
    SendDebugMessage(content)
    SendEmbedMessage(components)
