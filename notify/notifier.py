# -*- coding: utf-8 -*-
"""
notify/notifier.py — 디스코드 발송 (sauron DiscordMsg 이식 + 메시지 edit)

- send_new(notice, dept) -> (channel_id, message_id)      : 공지 발송(요약이 있으면 함께 담긴다)
- send_summarized(notice, dept) -> (channel_id, message_id): 요약까지 끝난 공지 1회 발송(기본 경로)
- edit_summary(channel_id, message_id, notice, dept)      : 2단 발송에서 요약 도착 후 메시지 수정
- debug(text)                                            : 감시채널 디버그 임베드
DRY_RUN(또는 토큰 없음): 실제 전송 대신 로그 + 가짜 message_id 반환(테스트용).
멘션 없음: 공지·디버그 모두 역할/유저 멘션을 붙이지 않는다(allowed_mentions로 이중 차단).
"""
import json
import os
import re
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


def _footer(text, icon_url=None):
    """임베드 footer. 아이콘이 없으면 icon_url 키 자체를 뺀다(디스코드는 빈 URL을 거부함)."""
    f = {"text": text}
    if icon_url:
        f["icon_url"] = icon_url
    return f


def _split_headline(content, limit=240):
    """디버그 내용 → (제목, 본문). 첫 줄을 임베드 제목으로 올린다.
    공지 알림처럼 제목에 요점이 있어야 폰 푸시에서 무슨 일인지 바로 보인다
    (제목이 항상 '디버그 메시지'면 알림만 보고는 알 수 없다)."""
    lines = [ln.rstrip() for ln in (content or "").splitlines()]
    head = next((ln for ln in lines if ln.strip()), "")
    rest = lines[lines.index(head) + 1:] if head in lines else lines
    title = re.sub(r"[*`]", "", re.sub(r"^\s*[#>]+\s*", "", head)).strip()   # 굵게·코드표시만 제거
    return (title[:limit - 1] + "…") if len(title) > limit else title, "\n".join(rest).strip()


def _footer_icon():
    """디버그 임베드 푸터 아이콘 URL(없으면 빈값 = 아이콘 없음).
    ⚠ 파일 첨부(attachment://)를 쓰지 않는다: 첨부가 있으면 디스코드 폰 푸시가 임베드 내용 대신
      '이미지를 보냈습니다'로 덮어써서, 무슨 문제인지 알림만 보고는 알 수 없다(공지 알림은 임베드만
      보내서 이 문제가 없다). 아이콘이 필요하면 만료되지 않는 URL을 ICON_DEBUG에 넣는다."""
    return config.ICON_DEBUG


class Notifier:
    def __init__(self, logger=None, dst="null", debug_channel_id=None, mono_channel_id=None):
        """dst: 'null'(전송안함) | 'mono'(통합채널) | 'poly'(각 학과채널) | '<채널ID>'(명시 채널).
        mono/debug 채널ID: build_components가 DB(app_meta, setup_guild가 저장)에서 읽어 주입."""
        self.token = _load_token()
        self.logger = logger
        self.dst = str(dst or "null")
        self.send_enabled = (self.dst != "null")   # 보낼 의사(dst != null)
        self.dry = not self.token                  # 실제 POST 불가(토큰 없음) → 시뮬레이션
        self.debug_channel_id = debug_channel_id or None
        self.mono_channel_id = mono_channel_id or None
        self._fake = 0
        self._poly_warned = set()                  # poly 폴백 경고 학과별 1회만
        self._debug_warned = False                 # 감시채널 미설정 경고 1회만(내용은 매번 로그)

    def _log(self, msg):
        (self.logger.info if self.logger else print)(msg)

    def _headers(self):
        return {"Authorization": f"Bot {self.token}", "Content-Type": "application/json"}

    def _post(self, channel_id, embed):
        if self.dry:
            self._fake += 1
            self._log(f"[DRY send] ch={channel_id} :: {embed['title']}")
            return {"id": f"dry-{self._fake}"}
        # 멘션 안 함: content 자체를 안 싣고, allowed_mentions로도 전부 억제(본문에 멘션 문자열이 섞여도 핑 방지).
        payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
        r = requests.post(f"{API}/channels/{channel_id}/messages",
                          headers=self._headers(), data=json.dumps(payload), timeout=30)
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

    # ── 임베드 구성 (제목 + 요약 + 링크필드 + 푸터). ────────────────────────────────────
    def _embed(self, notice, dept):
        summary = (notice.get("summary") or "").strip()
        status = notice.get("status")
        if summary:
            desc = f"​\n{summary}\n\n{SUMMARY_DISCLAIMER}\n​"     # 성공: 요약 + 면책 문구
        elif status == "summary_failed":
            desc = f"​\n{SUMMARY_FAIL_NOTE}\n​"                    # 실패: 대체 문구
        elif status == "no_content":
            desc = f"​\n{SUMMARY_NO_CONTENT_NOTE}\n​"              # 내용없음(#3): 제목만/이미지 로드 실패
        else:
            desc = "​"                                            # 요약 대기(2단 발송의 1단)
        return {
            "title": f"📢 {notice['title']}",
            "description": desc,
            "color": NOTICE_COLOR,
            "fields": [{
                "name": "🔗 링크",
                "value": f"[▶자세히 보기]({notice['url']})",
                "inline": True,
            }],
            "footer": _footer(dept.get("name_ko", ""), dept.get("icon_url") or config.ICON_DEFAULT),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _resolve_channel(self, dept):
        """dst에 따라 발송 채널 결정(멘션 없음 — 구독자는 역할로 채널을 보는 것으로 충분)."""
        if self.dst == "poly":                                   # 각 학과 전용 채널
            return dept.get("discord_channel_id")
        if self.dst == "mono":                                   # 통합채널 몰빵
            return self.mono_channel_id
        if self.dst.isdigit():                                   # 명시한 단일 채널
            return self.dst
        return None                                              # null 등 → 전송 없음

    # ── 공개 API ──────────────────────────────────────
    def send_new(self, notice, dept):
        channel_id = self._resolve_channel(dept)
        if not channel_id:
            # 대상 채널 없음 → 통합채널 폴백. 왜 폴백했는지 로그로 드러낸다(조용한 오배송 방지).
            if self.dst == "poly":
                did = dept.get("dept_id") or ""
                if did not in self._poly_warned:
                    self._poly_warned.add(did)
                    self._log(f"[경고] poly인데 '{dept.get('name_ko') or did}' 학과채널 미배정 "
                              f"→ 통합채널 폴백. `python -m notify.setup_guild` 로 채널 생성 필요")
            elif self.dst == "mono":
                self._log("[경고] --dst mono 인데 통합채널 미설정 → 발송 대상 없음. "
                          "`python -m notify.setup_guild` 실행(통합채널 생성) 또는 --dst <채널ID>를 쓰세요")
            channel_id = self.mono_channel_id or "null"
        res = self._post(channel_id, self._embed(notice, dept))
        return channel_id, res["id"]

    def send_summarized(self, notice, dept):
        """요약까지 끝난 공지를 한 번에 발송(config.SEND_AFTER_SUMMARY의 기본 경로).
        실패 문구가 사용자에게 보이는 순간의 감시채널 디버그는 edit_summary와 똑같이 여기서 책임진다
        — 발송이 실패해도 디버그는 나간다(오히려 그때가 더 중요) → finally."""
        try:
            return self.send_new(notice, dept)
        finally:
            if notice.get("status") == "summary_failed":
                self.debug_summary_failed(notice, dept)

    def edit_summary(self, channel_id, message_id, notice, dept):
        """요약 결과를 원 메시지에 반영. 실패 상태면 감시채널 디버그도 여기서 함께 보낸다.

        발송 지점을 호출부(worker)가 아니라 '실패 문구를 그리는 곳'에 두는 이유:
        SUMMARY_FAIL_NOTE가 뜨는데 디버그는 안 오는 경로(redo·재적재·edit 실패 등)를 원천 차단.
        패치가 실패해도 디버그는 나간다(오히려 그때가 더 중요) — finally."""
        try:
            if channel_id:
                self._patch(channel_id, message_id, self._embed(notice, dept))   # 임베드만 갱신
        finally:
            if notice.get("status") == "summary_failed":
                self.debug_summary_failed(notice, dept)

    def debug_summary_failed(self, notice, dept=None):
        """'요약을 실패하였습니다'가 사용자에게 보이는 순간의 디버그 알림(사유·링크 포함)."""
        dept = dept or {}
        reason = (notice.get("fail_reason") or "사유 미기록").strip()
        where = dept.get("name_ko") or notice.get("dept_id") or "-"
        url = notice.get("url")
        self.debug(f"**요약 실패** · {where}\n"
                   f"{(notice.get('title') or '')[:80]}\n"
                   f"사유: `{reason[:300]}`"
                   + (f"\n[▶공지 보기]({url})" if url else ""))

    def debug(self, content):
        icon_url = _footer_icon()
        title, body = _split_headline(content)
        embed = {
            "title": f"⚠️ {title}" if title else "⚠️ 디버그 메시지",
            "description": f"​\n{body}" if body else "​",
            "color": DEBUG_COLOR,
            "footer": _footer(config.BOT_DISPLAY_NAME, icon_url),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        channel = self.debug_channel_id
        if not (channel and self.token):
            # 조용히 버리면 '디버그가 안 오니까 문제도 없다'로 읽힌다(실제로 그렇게 192건이 묻혔다).
            # 못 보내는 이유를 1회 경고하고, 내용 자체는 매번 로그에 남긴다.
            if not self._debug_warned:
                self._debug_warned = True
                why = "감시채널ID 없음(app_meta.debug_channel_id)" if not channel else "봇 토큰 없음"
                self._log(f"[디버그 발송 불가] {why} → 디버그는 로그에만 남습니다. "
                          f"`python -m notify.setup_guild` 실행으로 감시채널을 등록하세요")
            self._log(f"[디버그(미발송)] {content}")
            return
        try:
            self._post_debug(channel, embed)
        except Exception as e:
            self._log(f"[debug 전송 실패] {e}")

    def _post_debug(self, channel_id, embed):
        # debug는 dst=null(dry)여도 전송(감시 목적). 토큰 있을 때만. 임베드만(첨부 없음 — 위 _footer_icon 참고).
        payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
        r = requests.post(f"{API}/channels/{channel_id}/messages",
                          headers=self._headers(), data=json.dumps(payload), timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"디버그 전송 실패 {r.status_code} (ch={channel_id}): {r.text[:150]}")
        return r.json()
