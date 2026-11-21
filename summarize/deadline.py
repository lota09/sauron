# -*- coding: utf-8 -*-
"""
summarize/deadline.py — 공지의 '지금 해야 하는 일의 마감일' (LLM-as-Jev 방식).

LLM 은 날짜를 쓰지 않는다. 고르기만 한다.
  1) 날짜 후보: 본문 + OCR 텍스트에서 정규식으로 뽑는다(값은 원문 그대로 → 지어낸 날짜·'202026' 같은 숫자 깨짐 없음).
     후보가 없으면 LLM 을 부르지 않고 state='none'.
  2) 선택: 로컬 LLM 에 원문 + 질문 + 기호 붙은 선택지(A, B, …, 마지막은 '해당 없음')를 주고
     max_tokens=1 로 첫 토큰의 확률(logprobs)을 읽는다. 생성 없음 → 공지 1건에 수 초.
  3) 판정: 고른 기호의 확률이 DEADLINE_MIN_PROB 미만이거나 호출이 실패하면 'unknown'.
     '해당 없음'을 고르면 'none'. 그 외 'set' + 날짜.

결과: {"state": "set"|"none"|"unknown", "at": "YYYY-MM-DD" | "YYYY-MM-DDTHH:MM" | None, "conf": float|None}
  · at 은 한국 시각(KST) 기준. 시각이 원문에 없으면 날짜만(그날 끝까지로 해석).
실측(2026-11-12, gemma-4-e4b, 직접 만든 3건): 정답 3/3, 확률 0.991~0.999, 건당 4.7~7.7s.
"""
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone

import requests

import config
from summarize.llm import html_to_text

log = logging.getLogger("sauron.deadline")
KST = timezone(timedelta(hours=9))
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_WD = r"(?:\s*\(\s*[월화수목금토일](?:요일)?\s*\))?"          # (화) (화요일)

# 연-월-일: 2026.10.30. / 2026-10-30 / 2026/10/30 / 2026년 10월 30일 / 26.10.30
_YMD = re.compile(r"(?<![\d.])(20\d{2}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?\.?(?![\d])" + _WD)
# 월-일: 10월 30일
_MD_KO = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일" + _WD)
# 월-일: 10/30, 10.30.(금), 10.30(금), ~ 11.20 — 소수(3.5점)·버전과 헷갈리지 않게 '.'·요일·~ 중 하나가 붙어야 한다
_MD_NUM = re.compile(
    r"(?<![\d.])(?P<pre>~\s*)?(\d{1,2})\s*(?P<sep>[./])\s*(\d{1,2})(?P<dot>\.)?"
    r"(?P<wd>\s*\(\s*[월화수목금토일](?:요일)?\s*\))?(?![\d])"
)
# 범위의 끝에 일(日)만: '11월 3일 ~ 20일', '11.3 ~ 20.(금)'
_D_ONLY = re.compile(r"~\s*(\d{1,2})\s*(?:일|\.)" + _WD)
# 날짜 바로 뒤 시각: 18:00 / 오후 6시 / 18시 30분
_TIME = re.compile(r"^\s*[,，]?\s*(?:까지\s*)?(오전|오후)?\s*(\d{1,2})\s*(?::\s*(\d{2})|시(?:\s*(\d{1,2})\s*분)?)")


def _mk(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _guess_year(m, d, ref):
    """연도 없는 날짜 → 게시일에서 가장 가까운 연도(12월 공지의 '1/5'는 다음 해)."""
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        x = _mk(y, m, d)
        if x and (best is None or abs((x - ref).days) < abs((best - ref).days)):
            best = x
    return best


def _time_after(text, pos):
    m = _TIME.match(text[pos:pos + 16])
    if not m:
        return None
    ampm, h, mi1, mi2 = m.group(1), int(m.group(2)), m.group(3), m.group(4)
    mi = int(mi1 or mi2 or 0)
    if ampm == "오후" and h < 12:
        h += 12
    if ampm == "오전" and h == 12:
        h = 0
    if h > 24 or mi > 59:
        return None
    if h == 24:
        h, mi = 23, 59
    return h, mi


def _snippet(text, start, end, width=None):
    """후보가 나온 줄(앞뒤 문맥) — 선택지에 원문을 그대로 보여 준다.
    짧게(DEADLINE_SNIPPET_CHARS): 선택지 토큰이 곧 처리 시간이다(이 폰의 프롬프트 처리 ≈15 토큰/s, 실측).
    날짜만 따로 있는 줄('2026. 11. 13.(금)')이면 앞 줄(항목 이름)을 붙인다."""
    width = width or config.DEADLINE_SNIPPET_CHARS
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    line = text[ls:len(text) if le < 0 else le]
    c = start - ls                                   # 줄 안에서 날짜 위치
    if len(_NOT_DATEISH.sub("", line)) < 3 and ls > 1:
        ps = text.rfind("\n", 0, ls - 1) + 1
        prev = text[ps:ls - 1].strip()[-20:]
        if prev:
            line, c = f"{prev} / {line}", c + len(prev) + 3
    line = line.strip()
    if len(line) > width:
        a = max(0, c - width // 2)
        line = ("…" if a else "") + line[a:a + width].strip() + "…"
    return re.sub(r"\s+", " ", line)


_BOARD_META = re.compile(r"등록일|작성일|게시일|조회수|작성자|조회\s*:")
_NOT_DATEISH = re.compile(r"[\d\s.:~\-/()월화수목금토일년일시분까지]")


def extract_candidates(text, ref):
    """text 에서 날짜 후보 [{"date": date, "time": (h, m)|None, "snippet": str}] (날짜·시각 순, 중복 제거).
    ref(date) = 게시일. 게시일보다 이른 날짜는 '지금 할 일의 마감'이 될 수 없어 뺀다."""
    hits = []                                     # (start, end, date)
    for m in _YMD.finditer(text):
        y = int(m.group(1))
        y = y + 2000 if y < 100 else y
        d = _mk(y, int(m.group(2)), int(m.group(3)))
        if d:
            hits.append((m.start(), m.end(), d))
    taken = [(a, b) for a, b, _ in hits]
    inside = lambda a, b: any(a < tb and b > ta for ta, tb in taken)      # noqa: E731
    for m in _MD_KO.finditer(text):
        if not inside(m.start(), m.end()):
            d = _guess_year(int(m.group(1)), int(m.group(2)), ref)
            if d:
                hits.append((m.start(), m.end(), d))
    taken = [(a, b) for a, b, _ in hits]
    for m in _MD_NUM.finditer(text):
        if inside(m.start(), m.end()):
            continue
        if not (m.group("sep") == "/" or m.group("dot") or m.group("wd") or m.group("pre")):
            continue
        mo, dd = int(m.group(2)), int(m.group(4))
        if not (1 <= mo <= 12 and 1 <= dd <= 31):
            continue
        d = _guess_year(mo, dd, ref)
        if d:
            hits.append((m.start(), m.end(), d))
    hits.sort()
    # '~ 20일': 바로 앞 후보의 연·월을 이어받는다
    taken = [(a, b) for a, b, _ in hits]
    for m in _D_ONLY.finditer(text):
        if inside(m.start(), m.end()):
            continue
        prev = [h for h in hits if h[1] <= m.start() and m.start() - h[1] < 40]
        if prev:
            p = prev[-1][2]
            d = _mk(p.year, p.month, int(m.group(1)))
            if d and d >= p:
                hits.append((m.start(), m.end(), d))
    hits.sort()

    out, seen = [], set()
    for a, b, d in hits:
        if d < ref:
            continue
        # 게시판 머리글의 등록일·작성일: 이름표는 날짜 '앞'에 온다. 뒤까지 보면 바로 다음 줄의 '등록일' 때문에
        #   진짜 마감('모집기간 ~2026.11.30 17:00\n등록일')이 빠진다(2026-11-21 발견, 취업 게시판).
        if _BOARD_META.search(text[max(0, a - 12):a]):
            continue
        t = _time_after(text, b)
        key = (d, t)
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": d, "time": t, "snippet": _snippet(text, a, b)})
    out.sort(key=lambda c: (c["date"], c["time"] or (23, 59)))
    # 같은 날짜가 '시각 있음/없음'으로 둘 다 있으면 시각 있는 쪽만 남긴다
    timed = {c["date"] for c in out if c["time"]}
    out = [c for c in out if c["time"] or c["date"] not in timed]
    return out


def _fmt(c):
    d = c["date"]
    s = f"{d.isoformat()}({'월화수목금토일'[d.weekday()]})"
    if c["time"]:
        s += f" {c['time'][0]:02d}:{c['time'][1]:02d}"
    return s


def _at(c):
    d = c["date"]
    return f"{d.isoformat()}T{c['time'][0]:02d}:{c['time'][1]:02d}" if c["time"] else d.isoformat()


def build_prompt(title, body, cands, ref, prefix=None):
    """prefix(요약 때 보낸 프롬프트)가 있으면 그 뒤에 질문만 잇는다 → 서버가 본문 부분 KV 캐시를 재사용.
    없으면 제목·원문을 직접 넣은 단독 프롬프트."""
    opts = [f"{LABELS[i]}. {_fmt(c)} — \"{c['snippet']}\"" for i, c in enumerate(cands)]
    none = LABELS[len(cands)]
    opts.append(f"{none}. 해당 없음 (학생이 지금 해야 할 일의 마감이 없음)")
    tail = (f"질문: {config.DEADLINE_QUESTION}\n선택지:\n" + "\n".join(opts) +
            "\n\n선택지 기호 한 글자로만 답하라.")
    if prefix:
        msg = (f"{prefix}\n\n---\n위의 요약 지시는 무시하고, 위 공지에 대해 아래 질문에만 답하라.\n"
               f"[공지 게시일] {ref.isoformat()}\n" + tail)
    else:
        body = body[:config.DEADLINE_MAX_INPUT_CHARS]
        msg = (f"[공지 제목]\n{title}\n\n[공지 게시일]\n{ref.isoformat()}\n\n[공지 원문]\n{body}\n\n" + tail)
    return msg, none


def choose(title, body, cands, ref, base_url=None, model=None, api_key=None, prefix=None):
    """로컬 LLM 에 고르게 한다 → (선택지 index | None(=해당 없음), 확률). 호출 실패는 예외."""
    msg, none = build_prompt(title, body, cands, ref, prefix)
    valid = LABELS[:len(cands) + 1]
    payload = {"messages": [{"role": "user", "content": msg}], "max_tokens": 1, "temperature": 0,
               "logprobs": True, "top_logprobs": 20, "stream": False}
    if model and model != "auto":
        payload["model"] = model
    h = {"Content-Type": "application/json"}
    if api_key and api_key not in ("sk-none", "not-needed", ""):
        h["Authorization"] = f"Bearer {api_key}"
    r = requests.post(f"{(base_url or config.LLM_BASE_URL).rstrip('/')}/chat/completions", json=payload,
                      headers=h, timeout=(config.LLM_CONNECT_TIMEOUT, config.DEADLINE_TIMEOUT))
    r.raise_for_status()
    ch = r.json()["choices"][0]
    probs = {}
    for x in (ch.get("logprobs") or {}).get("content", [{}])[0].get("top_logprobs", []):
        k = x["token"].strip().upper()
        if len(k) == 1 and k in valid:
            probs[k] = probs.get(k, 0.0) + math.exp(x["logprob"])       # 'B'·'b'·' B' 합산
    if not probs:                                    # logprobs 를 안 주는 서버 → 답 글자만, 확률 모름
        k = (ch.get("message", {}).get("content") or "").strip()[:1].upper()
        if k not in valid:
            raise ValueError(f"선택지 밖 응답: {k!r}")
        probs = {k: float("nan")}
    k = max(probs, key=probs.get)
    tot = sum(v for v in probs.values() if not math.isnan(v)) or 1.0
    p = probs[k] / tot if not math.isnan(probs[k]) else float("nan")
    return (None if k == none else valid.index(k)), p


def analyze(title, content_html, ocr_text=None, ref=None, base_url=None, model=None, prefix=None):
    """공지 1건 → {"state", "at", "conf", "n"}. 예외를 밖으로 내지 않는다(실패=unknown)."""
    ref = ref or datetime.now(KST).date()
    body = html_to_text(content_html or "")
    if ocr_text:
        body = (body + "\n\n" + ocr_text).strip()
    cands = extract_candidates(f"{title}\n{body}", ref)
    if not cands:
        return {"state": "none", "at": None, "conf": None, "n": 0}
    if len(cands) > config.DEADLINE_MAX_CHOICES:
        cands = cands[:config.DEADLINE_MAX_CHOICES]               # 이른 날짜 우선(마감은 대개 앞쪽)
    try:
        idx, p = choose(title, body, cands, ref, base_url=base_url, model=model,
                        api_key=config.LLM_API_KEY, prefix=prefix)
    except Exception as e:
        log.warning("[마감] 선택 호출 실패 → unknown: %s: %s", type(e).__name__, str(e)[:150])
        return {"state": "unknown", "at": None, "conf": None, "n": len(cands)}
    conf = None if math.isnan(p) else round(p, 3)
    if conf is None or conf < config.DEADLINE_MIN_PROB:
        return {"state": "unknown", "at": None, "conf": conf, "n": len(cands)}
    if idx is None:
        return {"state": "none", "at": None, "conf": conf, "n": len(cands)}
    return {"state": "set", "at": _at(cands[idx]), "conf": conf, "n": len(cands)}


def to_unix(at):
    """'YYYY-MM-DD'(그날 끝 23:59) | 'YYYY-MM-DDTHH:MM' (KST) → (유닉스 초, 시각 있음 여부)."""
    if not at:
        return None, False
    if "T" in at:
        dt = datetime.strptime(at, "%Y-%m-%dT%H:%M").replace(tzinfo=KST)
        return int(dt.timestamp()), True
    dt = datetime.strptime(at, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=KST)
    return int(dt.timestamp()), False


def discord_line(notice):
    """디스코드 임베드용 마감 줄. 마감이 정해진(set) 공지만, 아니면 None(줄 자체를 뺀다).
    <t:…> 는 보는 사람 화면에서 그려지고 '8일 후 → 하루 전'으로 저절로 바뀐다(메시지 수정 없음)."""
    if notice.get("deadline_state") != "set":
        return None
    ts, timed = to_unix(notice.get("deadline_at"))
    if ts is None:
        return None
    return f"⏰ <t:{ts}:{'f' if timed else 'D'}> 까지 · <t:{ts}:R> 마감"
