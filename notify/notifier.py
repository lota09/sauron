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
# 요약 성공 시 맨 끝에 붙는 면책 문구(마크다운 인용). 실패/내용없음 시 요약 자리에 뜨는 문구.
SUMMARY_DISCLAIMER = "> AI는 실수 할 수 있습니다. 정확한 정보는 해당 공지를 확인해주세요."
SUMMARY_FAIL_NOTE = "> 요약을 실패하였습니다."
SUMMARY_NO_CONTENT_NOTE = "> 요약할 내용이 없습니다."


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
    def __init__(self, logger=None, dst="null", debug_channel_id=None):
        """dst: 'null'(전송안함) | 'mono'(통합채널) | 'poly'(각 학과채널) | '<채널ID>'(명시 채널).
        debug_channel_id: 감시(디버그) 채널. 미지정 시 config.DEBUG_CHANNEL_ID(수동) 사용.
        보통 build_components가 config 또는 DB(app_meta, setup_guild 자동생성분)에서 채워 넘긴다."""
        self.token = _load_token()
        self.logger = logger
        self.dst = str(dst or "null")
        self.send_enabled = (self.dst != "null")   # 보낼 의사(dst != null)
        self.dry = not self.token                  # 실제 POST 불가(토큰 없음) → 시뮬레이션
        self.debug_channel_id = debug_channel_id or config.DEBUG_CHANNEL_ID or None
        self._fake = 0
        self._poly_warned = set()                  # poly 폴백 경고 학과별 1회만

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
            raise RuntimeError(f"디스코드 전송 실패 {r.status_code} (ch={channel_id}): {r.text[:200]}")
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

    # ── 임베드 구성 (기존 스타일: 제목 + 요약 + 링크필드 + 푸터) ─────
    def _embed(self, notice, dept, mention):
        summary = (notice.get("summary") or "").strip()
        status = notice.get("status")
        if summary:
            desc = f"​\n{summary}\n\n{SUMMARY_DISCLAIMER}\n​"     # 성공: 요약 + 면책 문구
        elif status == "summary_failed":
            desc = f"​\n{SUMMARY_FAIL_NOTE}\n​"                    # 실패: 대체 문구
        elif status == "no_content":
            desc = f"​\n{SUMMARY_NO_CONTENT_NOTE}\n​"              # 내용없음(#3): 제목만/OCR실패
        else:
            desc = "​"                                            # 발송 직후(요약 대기)
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
        """dst에 따라 (channel_id, mention) 결정."""
        if self.dst == "poly":                                   # 각 학과 전용 채널(+@everyone)
            return dept.get("discord_channel_id"), "@everyone"
        if self.dst == "mono":                                   # 통합채널 몰빵(무멘션)
            return config.MONO_CHANNEL_ID, ""
        if self.dst.isdigit():                                   # 명시한 단일 채널(무멘션)
            return self.dst, ""
        return None, ""                                          # null 등 → 전송 없음

    # ── 공개 API ──────────────────────────────────────
    def send_new(self, notice, dept):
        channel_id, mention = self._resolve_channel(dept)
        if not channel_id:
            # 대상 채널 없음 → 통합채널 폴백. 왜 폴백했는지 로그로 드러낸다(조용한 오배송 방지).
            if self.dst == "poly":
                did = dept.get("dept_id") or ""
                if did not in self._poly_warned:
                    self._poly_warned.add(did)
                    self._log(f"[경고] poly인데 '{dept.get('name_ko') or did}' 학과채널 미배정 "
                              f"→ 통합채널 폴백. `python -m notify.setup_guild` 로 채널 생성 필요")
            elif self.dst == "mono":
                self._log("[경고] --dst mono 인데 MONO_CHANNEL_ID 미설정 → 발송 대상 없음. "
                          "config에 MONO_CHANNEL_ID를 넣거나 --dst <채널ID>를 쓰세요")
            channel_id = config.MONO_CHANNEL_ID or "null"
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
        channel = self.debug_channel_id
        if not (channel and self.token):
            return          # 감시채널 미설정(setup_guild 미실행) 또는 토큰 없음: 스킵(로그만)
        try:
            self._post_debug(channel, embed)
        except Exception as e:
            self._log(f"[debug 전송 실패] {e}")

    def _post_debug(self, channel_id, embed):
        # debug는 dst=null(dry)여도 전송(감시 목적). 토큰 있을 때만.
        r = requests.post(f"{API}/channels/{channel_id}/messages",
                          headers=self._headers(), data=json.dumps({"embeds": [embed]}), timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"디버그 전송 실패 {r.status_code} (ch={channel_id}): {r.text[:150]}")
        return r.json()
