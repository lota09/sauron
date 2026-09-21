# -*- coding: utf-8 -*-
"""
summarize/llm.py — 요약기 (교체가능 인터페이스)

Summarizer.summarize(title, content_html, image_urls=None) -> (summary:str, engine:str)
  기본: OpenAICompatSummarizer (localhost/LAN, OpenAI 호환 /chat/completions)
  - 느슨한 텍스트 요약(엄격 JSON 강요 X), 동적 길이
  - 거절/헛소리·과소길이 감지 → SummaryError
  - E2B 실패 시 LLM_MODEL_FALLBACK(E4B)로 승격 재시도

무외부의존 원칙: openai 패키지 없이 requests로 직접 호출.
"""
import json
import logging
import re
import threading
import time
import requests
from bs4 import BeautifulSoup

import config

# 크롤러가 구성한 'sauron' 로거의 자식 — 핸들러를 물려받아 logs/sauron.log 로 간다.
# LLM 호출은 실패가 잦고 외부 요인(서버 OOM·재시작)이 많아, 요청 1건마다 모델·크기·시간·결과를
# 남긴다. 사후에 'DB엔 [conn] 뿐'인 상태로 추적 불가해지는 걸 막기 위함.
log = logging.getLogger("sauron.llm")

# 종료(에러/Ctrl-C) 시 진행 중 스트림 생성을 끊기 위한 신호(워커 스레드와 공유).
SHUTDOWN = threading.Event()


def request_shutdown():
    """호출 시 진행 중인 LLM 스트림이 다음 토큰/타임아웃 시점에 중단된다(연결 종료 → 서버 생성 중지)."""
    SHUTDOWN.set()

# 요약 프롬프트는 config.py(LLM_SYSTEM_PROMPT / LLM_USER_TEMPLATE)에서 관리 — 스타일 조정 지점.

# 거절/헛소리 감지 — '인공지능'·'모델' 단독은 정상 공지에도 나오므로 사용 금지.
# 반드시 1인칭 자기지칭(저는/제가/I am) + 거절이 결합된 경우만 잡는다(오탐 최소화).
REFUSAL_PATTERNS = [
    r"저는\s*(단순한?\s*)?(대규모\s*)?(언어\s*모델|인공지능|ai)",   # "저는 (단순) 언어모델/인공지능"
    r"(저는|제가)[^.\n]{0,25}(할\s*수\s*없|접근할\s*수\s*없|권한이\s*없|도와드릴\s*수\s*없|제공할\s*수\s*없)",
    r"as an ai\b",
    r"i am (an?|your)\s+(ai|assistant|language model)",
    r"i (cannot|can'?t|am unable) (help|assist|provide|access|answer)",
    r"i'?m sorry,?\s+but i",
    r"죄송하지만[^.\n]{0,20}(할\s*수\s*없|없습니다|불가능)",
]


class SummaryError(Exception):
    """요약 실패 기저 예외. reason으로 사유 분류(디버그/재시도 정책용)."""
    reason = "generic"


class EmptyContentError(SummaryError):
    """본문·이미지 모두 없어 요약할 내용이 없음(#3). LLM 호출 안 함 → 재시도 안 함."""
    reason = "empty"


class ModelNotFoundError(SummaryError):
    """서버는 응답하나 모델명이 불일치(HTTP 400/404 + 'model'). 폴백 모델로 교체 재시도."""
    reason = "model"


class ConnectionErrorLLM(SummaryError):
    """연결/타임아웃/서버 무응답. 대기 후 재시도(환경적이라 같은 요청도 의미 있음)."""
    reason = "connection"


class ValidationErrorLLM(SummaryError):
    """언어이탈/거절/과소길이/반복붕괴. greedy=결정론이라 재시도는 입력을 바꿔야 함(폴백/프롬프트변형)."""
    reason = "validation"


class ServerError(SummaryError):
    """서버 응답O·모델명OK인데 실패(HTTP 5xx / 빈 스트림 / 파싱실패 등). 'LLM 재시작'으로 풀릴 여지."""
    reason = "server"


def _err_message(r):
    """API 오류 응답에서 사람이 읽을 error.message 추출(없으면 원문 일부). 예: OlliteRT 컨텍스트 초과 안내."""
    try:
        m = (r.json().get("error") or {}).get("message")
        if m:
            return m.strip()
    except Exception:
        pass
    return (getattr(r, "text", "") or "")[:500]


def _classify_http(status, body):
    b = (body or "").lower()
    if status in (400, 404) and "model" in b:
        return ModelNotFoundError(f"HTTP {status}: {body}")
    return ServerError(f"HTTP {status}: {body}")


def _interruptible_sleep(seconds):
    """SHUTDOWN 신호를 존중하며 대기(0.5초 단위 폴링)."""
    end = time.time() + max(0, seconds)
    while time.time() < end:
        if SHUTDOWN.is_set():
            return
        time.sleep(0.5)


DEFAULT_MODEL = "Gemma-4-E2B-it"  # 자동감지 실패 시 최후 폴백


def probe_backend(base_url=None, timeout=5):
    """LLM 백엔드 생존 점검(상태표시 전용 — 예외를 던지지 않는다).
    /health(model·status·uptime_seconds) → /v1/models(OpenAI 표준) 순으로 시도.
    반환: {ok, model, status, uptime, latency_ms, error, url}."""
    base = (base_url or config.LLM_BASE_URL).rstrip("/")
    out = {"ok": False, "model": None, "status": None, "uptime": None,
           "latency_ms": None, "error": None, "url": base}
    t0 = time.time()
    try:
        r = requests.get(f"{base}/health", timeout=timeout)
        out["latency_ms"] = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            try:
                d = r.json()
            except ValueError:
                d = {}
            out.update(ok=True, model=d.get("model"), status=d.get("status") or "ok",
                       uptime=d.get("uptime_seconds"))
            return out
        out["error"] = f"HTTP {r.status_code}"
    except Exception as e:
        out["error"] = type(e).__name__
    t0 = time.time()                       # /health 없는 백엔드(vLLM 등) → 표준 목록으로 재확인
    try:
        r = requests.get(f"{base}/models", timeout=timeout)
        out["latency_ms"] = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            data = r.json().get("data") or []
            out.update(ok=True, error=None, status="ok",
                       model=(data[0].get("id") if data else None))
        else:
            out["error"] = f"HTTP {r.status_code}"
    except Exception as e:
        out["error"] = out["error"] or type(e).__name__
    return out


def fetch_loaded_model(base_url, timeout=10):
    """서버에 로드된 모델명을 조회. /health(model+status, 가장 단순) → /v1/models(표준) 순. 실패 시 None."""
    base = (base_url or "").rstrip("/")
    # 1) /health — model 필드 하나 + status(준비상태 겸용). OlliteRT는 /v1/health 도 제공.
    try:
        r = requests.get(f"{base}/health", timeout=timeout)
        if r.status_code == 200:
            m = r.json().get("model")
            if m:
                return m
    except Exception:
        pass
    # 2) /v1/models — OpenAI 표준 목록 폴백
    try:
        r = requests.get(f"{base}/models", timeout=timeout)
        if r.status_code == 200:
            data = r.json().get("data") or []
            if data and data[0].get("id"):
                return data[0]["id"]
    except Exception:
        pass
    return None


def html_to_text(html: str) -> str:
    if not html:
        return ""
    try:
        txt = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    except Exception:
        txt = html
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", txt).strip()


_RUN_CHAR = re.compile(r"(.)\1{11,}")       # 같은 문자 12자 이상 연속 (예: '0000…')
_RUN_TOKEN = re.compile(r"(.{2,6}?)\1{4,}")  # 2~6자 단위가 5회 이상 반복 (예: '0:000:00…')


def strip_degenerate(text: str):
    """반복 붕괴(같은 문자/짧은토큰/동일 줄 반복) 제거. (정리본, 잘렸는지) 반환.
    좋은 앞부분은 보존하고 붕괴 시작점부터 잘라냄."""
    if not text:
        return text, False
    cut = False
    for rx in (_RUN_CHAR, _RUN_TOKEN):
        m = rx.search(text)
        if m:
            text = text[:m.start()]
            cut = True
    # 동일 줄이 연속 3회 이상이면 이후 반복 제거
    out, prev, run = [], None, 0
    for ln in text.split("\n"):
        s = ln.strip()
        if s and s == prev:
            run += 1
            if run >= 2:      # 세 번째 등장부터 버림
                cut = True
                continue
        else:
            prev, run = s, 0
        out.append(ln)
    return "\n".join(out).rstrip(), cut


# 한국어 공지에 거의 나오지 않는 스크립트(주입/이탈 신호): 키릴·데바나가리·아랍·가나·태국·히브리
_FOREIGN_SCRIPT = re.compile(r"[Ѐ-ӿऀ-ॿ؀-ۿ぀-ヿ฀-๿֐-׿]")
_HAN = re.compile(r"[一-鿿]")            # CJK 한자(한자 과다 = 중국어 의심)
_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")


def language_issue(text: str):
    """한국어 이탈 감지(결정론). 문제 사유 문자열 또는 None.
    영어 고유명사·URL·이메일(라틴)은 허용. 키릴/힌디/한자 과다/한글 결핍만 잡음."""
    if _FOREIGN_SCRIPT.search(text):
        return "외국문자 혼입(키릴/힌디/가나 등)"
    han = len(_HAN.findall(text))
    if han > config.LLM_MAX_HAN:
        return f"한자 과다({han})"
    hangul = len(_HANGUL.findall(text))
    latin = len(_LATIN.findall(text))
    if hangul + latin > 30 and hangul < config.LLM_MIN_HANGUL_RATIO * (hangul + latin):
        return "한글 비율 과소(비한국어 의심)"
    return None


def _validate(text: str) -> str:
    t = (text or "").strip()
    if len(t) < 8:
        raise ValidationErrorLLM("요약이 너무 짧음/비어있음")
    low = t.lower()
    for pat in REFUSAL_PATTERNS:
        if re.search(pat, low):
            raise ValidationErrorLLM(f"거절/헛소리 감지: {pat}")
    if config.LLM_ENFORCE_KOREAN:
        issue = language_issue(t)
        if issue:
            raise ValidationErrorLLM(f"언어 이탈: {issue}")
    return t


class OpenAICompatSummarizer:
    """
    OpenAI 호환 chat/completions 요약기.
    litertlm/Gemma 서버 호환을 위해: system 롤 없이 단일 user 메시지, 스트리밍, max_tokens.
    (검증된 형태 — llm_client/test.py, test3.py 기준)
    """

    def __init__(self, base_url=None, api_key=None, model=None,
                 fallback_model=None, timeout=None, max_input_chars=None,
                 stream=None, max_tokens=None):
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self.api_key = api_key or config.LLM_API_KEY
        self.model = model or config.LLM_MODEL
        self.fallback_model = fallback_model if fallback_model is not None else config.LLM_MODEL_FALLBACK
        self.timeout = timeout or config.LLM_TIMEOUT          # read timeout
        self.connect_timeout = config.LLM_CONNECT_TIMEOUT
        self.wall_timeout = config.LLM_WALL_TIMEOUT
        self.max_input_chars = max_input_chars or config.LLM_MAX_INPUT_CHARS
        self.stream = config.LLM_STREAM if stream is None else stream
        self.max_tokens = max_tokens or config.LLM_MAX_TOKENS
        t = str(config.LLM_TEMPERATURE).strip()
        self.temperature = float(t) if t not in ("", "None") else None
        fp = str(config.LLM_FREQUENCY_PENALTY).strip()
        self.frequency_penalty = float(fp) if fp not in ("", "None") else None

    def _compose_prompt(self, title, body_text):
        body = body_text[: self.max_input_chars] if len(body_text) > self.max_input_chars else body_text
        # Gemma엔 system 턴이 없음 → 지시문(config)을 user 메시지에 통합
        return f"{config.LLM_SYSTEM_PROMPT}\n\n{config.LLM_USER_TEMPLATE.format(title=title, body=body)}"

    def _payload(self, model, messages):
        p = {
            "model": model,
            "messages": messages,
            "stream": self.stream,
            "max_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            p["temperature"] = self.temperature
        if self.frequency_penalty is not None:  # 반복 억제(서버가 지원 시). 기본 미전송
            p["frequency_penalty"] = self.frequency_penalty
        return p

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key and self.api_key not in ("sk-none", "not-needed", ""):
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    _CONN_EXC = (requests.ConnectTimeout, requests.ReadTimeout, requests.ConnectionError,
                 requests.exceptions.ChunkedEncodingError, requests.Timeout)

    def _req_shape(self, messages):
        """요청 크기 요약(텍스트 글자수·이미지 장수·전송 KB). 서버가 큰 요청에서 죽는지 보려면 필요."""
        content = (messages[0] or {}).get("content")
        if isinstance(content, str):
            return len(content), 0, len(content.encode()) // 1024
        chars = sum(len(p.get("text", "")) for p in content if p.get("type") == "text")
        urls = [p["image_url"]["url"] for p in content if p.get("type") == "image_url"]
        kb = (chars + sum(len(u) for u in urls) * 3 // 4) // 1024
        return chars, len(urls), kb

    def _call(self, model, messages):
        url = f"{self.base_url}/chat/completions"
        payload = self._payload(model, messages)
        chars, nimg, kb = self._req_shape(messages)
        log.info("[LLM 요청] model=%s 텍스트 %d자 · 이미지 %d장 · 전송 %dKB · stream=%s "
                 "timeout(conn %ds/read %ds/wall %ds)", model, chars, nimg, kb, self.stream,
                 self.connect_timeout, self.timeout, self.wall_timeout)
        if self.stream:
            return self._call_stream(url, payload)
        t0 = time.time()
        try:
            r = requests.post(url, json=payload, headers=self._headers(),
                              timeout=(self.connect_timeout, self.timeout))
        except self._CONN_EXC as e:
            log.warning("[LLM 실패] %.1fs · %s (비스트림)", time.time() - t0, type(e).__name__)
            raise ConnectionErrorLLM(f"연결 실패: {type(e).__name__}") from e
        if r.status_code != 200:
            log.warning("[LLM 실패] %.1fs · HTTP %s · %s", time.time() - t0, r.status_code,
                        _err_message(r)[:200])
            raise _classify_http(r.status_code, _err_message(r))   # 모델오류 vs 서버오류 분류(응답 message 노출)
        data = r.json()
        try:
            out = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            log.warning("[LLM 실패] %.1fs · 응답 파싱 실패: %s", time.time() - t0, str(data)[:200])
            raise ServerError(f"응답 파싱 실패: {e} / {str(data)[:200]}")
        log.info("[LLM 응답] %.1fs · %d자 (비스트림)", time.time() - t0, len(out or ""))
        return out

    def _call_stream(self, url, payload):
        parts = []
        start = time.time()
        first_tok = None          # 첫 토큰까지 걸린 시간(프리필 지연) — 서버 부하 판단의 핵심 지표
        server_err = None         # 스트림 안에 실려 온 서버 오류 메시지
        stop = "정상종료"          # 스트림이 왜 끝났는지: [DONE] / wall / shutdown / 연결끊김
        try:
            with requests.post(url, json=payload, headers=self._headers(),
                               timeout=(self.connect_timeout, self.timeout), stream=True) as r:
                if r.status_code != 200:
                    log.warning("[LLM 실패] %.1fs · HTTP %s · %s", time.time() - start,
                                r.status_code, _err_message(r)[:200])
                    raise _classify_http(r.status_code, _err_message(r))
                for line in r.iter_lines(decode_unicode=True):
                    if SHUTDOWN.is_set():                          # 종료 신호 → 즉시 스트림 중단
                        stop = "종료신호"
                        break
                    if time.time() - start > self.wall_timeout:   # 총 벽시계 상한 → 중단(부분 보존)
                        stop = f"벽시계상한({self.wall_timeout}s)"
                        log.warning("[LLM 중단] 벽시계 상한 %ds 초과 → 부분 보존(%d조각). "
                                    "LLM_WALL_TIMEOUT 상향 또는 max_tokens 하향 검토",
                                    self.wall_timeout, len(parts))
                        break
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("error"):              # 서버가 스트림으로 오류를 보냄(예: 이미지 디코딩 실패)
                        e_ = obj["error"]
                        server_err = (e_.get("message") if isinstance(e_, dict) else str(e_)) or str(e_)
                        continue
                    ch = (obj.get("choices") or [{}])[0]
                    delta = ch.get("delta") or {}
                    tok = delta.get("content")
                    if tok:
                        if first_tok is None:
                            first_tok = time.time() - start
                        parts.append(tok)
        except self._CONN_EXC as e:
            # 두 경우를 가른다(requests는 스트리밍 중 읽기 타임아웃도 ConnectionError로 감싸서 던진다):
            #   · 읽기 시간초과 — LLM_TIMEOUT 동안 바이트가 안 옴. 첫 토큰 전이면 대개 긴 입력의 prefill이 느린 것.
            #   · 연결 끊김     — 서버가 생성 도중 죽음(OOM 등).
            got = sum(len(p) for p in parts)
            ftok = f"{first_tok:.1f}s" if first_tok is not None else "없음"
            timed_out = "Read timed out" in str(e) or isinstance(e, requests.ReadTimeout)
            if timed_out:
                what = (f"첫 토큰 {self.timeout}s 대기 초과" if first_tok is None
                        else f"토큰 사이 {self.timeout}s 침묵")
                log.warning("[LLM 시간초과] %.1fs · %s · 받은 %d자 → LLM_TIMEOUT(%ds) 초과(서버는 살아 있을 수 있음)",
                            time.time() - start, what, got, self.timeout)
                raise ConnectionErrorLLM(f"시간초과: {what}") from e
            log.warning("[LLM 끊김] %.1fs · %s · 첫토큰 %s · 받은 %d자 → 서버가 연결을 끊음",
                        time.time() - start, type(e).__name__, ftok, got)
            raise ConnectionErrorLLM(f"연결/스트림 실패: {type(e).__name__}") from e
        if not parts:
            if server_err:
                log.warning("[LLM 서버오류] %.1fs · %s", time.time() - start, server_err[:300])
                raise ServerError(f"서버 오류: {server_err[:300]}")
            log.warning("[LLM 빈응답] %.1fs · 스트림은 열렸으나 토큰 0개(%s)", time.time() - start, stop)
            raise ServerError("스트림 응답이 비어있음")   # 서버 응답O인데 생성 0 → 재시작 후보
        out = "".join(parts)
        log.info("[LLM 응답] %.1fs · 첫토큰 %.1fs · %d자 · %s",
                 time.time() - start, first_tok or 0.0, len(out), stop)
        return out

    def ensure_model(self):
        """LLM_MODEL='auto'(또는 빈값)면 서버에 로드된 모델을 1회 조회해 확정·캐시."""
        if self.model and str(self.model).strip().lower() not in ("", "auto"):
            return self.model
        self.model = fetch_loaded_model(self.base_url, min(self.timeout, 15)) or DEFAULT_MODEL
        return self.model

    def _generate(self, base_prompt, image_urls=None):
        """공통 생성+재시도 루프. image_urls(list)가 있으면 멀티모달(다중 이미지) 메시지로 구성.
        사유별 재시도 정책(config). 최종 실패 시 SummaryError(사유 누적)."""
        def build(prompt):
            if image_urls:
                content = [{"type": "text", "text": prompt}]
                content += [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]
            else:
                content = prompt
            return [{"role": "user", "content": content}]

        def attempt(model, prompt):
            raw = self._call(model, build(prompt))
            text, cut = strip_degenerate(raw)                             # 반복 붕괴 제거
            if cut:
                log.warning("[LLM 반복붕괴] %d자 → %d자로 절단(같은 문자/토큰/줄 반복)", len(raw), len(text))
            try:
                return _validate(text)
            except ValidationErrorLLM as e:
                # 검증 실패는 '무엇이 왜'가 안 남으면 재현이 불가능하다 → 앞머리를 함께 남긴다.
                log.warning("[LLM 검증실패] %s · 출력 %d자 :: %s", e, len(text),
                            text[:120].replace("\n", " "))
                raise

        model = self.model
        prompt = base_prompt
        retries = config.LLM_RETRY_LIMIT
        reasons = []
        while True:
            try:
                return attempt(model, prompt), model
            except ModelNotFoundError as e:
                reasons.append(f"[model] {e}")
                if self.fallback_model and model != self.fallback_model:
                    log.warning("[LLM 재시도] 모델명 오류 → 폴백 모델 %s (한도 미차감)", self.fallback_model)
                    model = self.fallback_model      # 모델명 오류: 폴백 교체(한도 미차감)
                    continue
                log.warning("[LLM 포기] 폴백 모델 없음 · 누적사유: %s", " / ".join(reasons))
                raise SummaryError(" / ".join(reasons))
            except ConnectionErrorLLM as e:
                reasons.append(f"[conn] {e}")
                if retries > 0 and not SHUTDOWN.is_set():
                    retries -= 1
                    log.warning("[LLM 재시도] 연결 실패 → %ds 대기 후 동일 요청 재시도 (남은 %d회)",
                                config.LLM_RETRY_WAIT_SEC, retries)
                    _interruptible_sleep(config.LLM_RETRY_WAIT_SEC)  # 대기 후 같은 요청 재시도
                    continue
                log.warning("[LLM 포기] 재시도 소진(또는 종료신호) · 누적사유: %s", " / ".join(reasons))
                raise SummaryError(" / ".join(reasons))
            except (ValidationErrorLLM, ServerError) as e:
                reasons.append(f"[{e.reason}] {e}")
                if retries > 0 and not SHUTDOWN.is_set():
                    retries -= 1
                    # greedy라도 프롬프트에 1토큰만 더해도 로짓 bias가 바뀌어 출력이 달라짐(리롤, 실측 입증).
                    used = config.LLM_RETRY_LIMIT - retries
                    prompt = base_prompt + f"\n\n(한국어로만, 반복 없이 간결히){'.' * used}"
                    if self.fallback_model and model != self.fallback_model:
                        model = self.fallback_model
                    log.warning("[LLM 재시도] %s → 프롬프트 변형 재시도 (모델 %s · 남은 %d회)",
                                e.reason, model, retries)
                    continue
                log.warning("[LLM 포기] 재시도 소진(또는 종료신호) · 누적사유: %s", " / ".join(reasons))
                raise SummaryError(" / ".join(reasons))

    def summarize(self, title, content_html, image_urls=None):
        """요약. 텍스트 프롬프트 하나에 (있으면) 이미지를 함께 첨부해 넘긴다(비전 전용 프롬프트 X).
        본문·이미지가 모두 없을 때만 EmptyContentError. engine은 이미지 첨부 시 'vision:<model>×N'."""
        self.ensure_model()
        body = html_to_text(content_html)
        if not body.strip() and not image_urls:
            raise EmptyContentError("본문·이미지 모두 없음")
        base = self._compose_prompt(title, body)
        if image_urls:
            base += config.LLM_VISION_HINT      # 이미지 있을 때만 '포스터를 읽어라' 지시 추가
        text, model = self._generate(base, image_urls=image_urls or None)
        engine = f"vision:{model}×{len(image_urls)}" if image_urls else model
        return text, engine


def default_summarizer():
    return OpenAICompatSummarizer()
