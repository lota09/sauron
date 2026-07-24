# -*- coding: utf-8 -*-
"""
notify/notifier.py — 디스코드 발송 (sauron DiscordMsg 이식 + 메시지 edit)

- send_new(notice, dept) -> (channel_id, message_id)   : 제목+링크+@everyone 즉시 발송(D1)
- edit_summary(channel_id, message_id, notice, dept)   : 요약 도착 후 메시지 수정(D2)
- debug(text)                                          : 감시채널 디버그 임베드
DRY_RUN(또는 토큰 없음): 실제 전송 대신 로그 + 가짜 message_id 반환(테스트용).
DEBUG_EN: 학과채널 대신 감시채널로, @everyone 제거.
"""
import json
import os
from datetime import datetime, timezone

import requests

import config

API = "https://discord.com/api/v10"
DEBUG_COLOR = 0xE74C3C
NOTICE_COLOR = 0x62C6C4


def _load_token():
    path = config.DISCORD_TOKEN_FILE
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("bot_token")
    except Exception:
        return None


class Notifier:
    def __init__(self, logger=None, debug=None, dry_run=None):
        self.token = _load_token()
        self.logger = logger
        self.debug_mode = config.DEBUG_EN if debug is None else debug   # True: 가짜 개발채널로
        self.dry = bool(dry_run) or not self.token   # 전송 안 함(--dryrun) 또는 토큰 없음
        self._fake = 0

    def _log(self, msg):
        (self.logger.info if self.logger else print)(msg)

    def _headers(self):
        return {"Authorization": f"Bot {self.token}", "Content-Type": "application/json"}

    def _post(self, channel_id, embed):
        if self.dry:
            self._fake += 1
            self._log(f"[DRY send] ch={channel_id} :: {embed['title']}")
            return {"id": f"dry-{self._fake}"}
        r = requests.post(f"{API}/channels/{channel_id}/messages",
                          headers=self._headers(), data=json.dumps({"embeds": [embed]}), timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"디스코드 전송 실패 {r.status_code}: {r.text[:200]}")
        return r.json()

    def _patch(self, channel_id, message_id, embed):
        if self.dry or str(message_id).startswith("dry-"):
            self._log(f"[DRY edit] ch={channel_id} mid={message_id} :: 요약 삽입")
            return {"id": message_id}
        r = requests.patch(f"{API}/channels/{channel_id}/messages/{message_id}",
                           headers=self._headers(), data=json.dumps({"embeds": [embed]}), timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"디스코드 수정 실패 {r.status_code}: {r.text[:200]}")
        return r.json()

    # ── 임베드 구성 ────────────────────────────────────
    def _embed(self, notice, dept, mention):
        summary = (notice.get("summary") or "").strip()
        desc = f"​\n{summary}\n​" if summary else "​"
        return {
            "title": f"📢 {notice['title']}",
            "description": desc,
            "color": NOTICE_COLOR,
            "fields": [{
                "name": "🔗 링크",
                "value": f"[▶자세히 보기]({notice['url']})\n​\n{mention}",
                "inline": True,
            }],
            "footer": {"text": dept.get("name_ko", ""), "icon_url": dept.get("icon_url") or config.ICON_DEFAULT},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _resolve_channel(self, dept):
        if self.debug_mode:
            # 개발용: 모든 학과 공지를 통합공지채널로 몰빵, @everyone 없음(개발채널 핑 방지)
            return config.DEBUG_NOTICE_CHANNEL_ID, ""
        return dept.get("discord_channel_id"), "@everyone"

    # ── 공개 API ──────────────────────────────────────
    def send_new(self, notice, dept):
        channel_id, mention = self._resolve_channel(dept)
        if not channel_id:
            # 채널 미배정(자동생성 전) → 개발/감시채널로 폴백 + 무멘션
            channel_id = config.DEBUG_NOTICE_CHANNEL_ID if self.debug_mode else config.DISCORD_DEBUG_CHANNEL_ID
            mention = ""
        res = self._post(channel_id, self._embed(notice, dept, mention))
        return channel_id, res["id"]

    def edit_summary(self, channel_id, message_id, notice, dept):
        _, mention = self._resolve_channel(dept)
        if not channel_id:
            return
        self._patch(channel_id, message_id, self._embed(notice, dept, mention))

    def debug(self, content):
        embed = {
            "title": "⚠️ 디버그 메시지",
            "description": f"​\n{content}",
            "color": DEBUG_COLOR,
            "footer": {"text": "사우론의 눈"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        channel = config.DEBUG_DEBUG_CHANNEL_ID if self.debug_mode else config.DISCORD_DEBUG_CHANNEL_ID
        try:
            self._post(channel, embed)
        except Exception as e:
            self._log(f"[debug 전송 실패] {e}")
