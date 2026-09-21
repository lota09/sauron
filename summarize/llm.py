# -*- coding: utf-8 -*-
"""
summarize/llm.py — 요약기 (교체가능 인터페이스)

Summarizer.summarize(title, content_html, ocr_text) -> (summary:str, engine:str)
  기본: OpenAICompatSummarizer (localhost/LAN, OpenAI 호환 /chat/completions)
  - 느슨한 텍스트 요약(엄격 JSON 강요 X), 동적 길이
  - 거절/헛소리·과소길이 감지 → SummaryError
  - E2B 실패 시 LLM_MODEL_FALLBACK(E4B)로 승격 재시도
  - 그래도 실패 → (옵션) ClovaSummarizer 폴백

무외부의존 원칙: openai 패키지 없이 requests로 직접 호출.
"""
import json
import re
import threading
import time
import requests
from bs4 import BeautifulSoup

import config

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
    """본문+OCR 모두 비어 요약할 내용이 없음(#3). LLM 호출 안 함 → 재시도 안 함."""
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

    def _call(self, model, messages):
        url = f"{self.base_url}/chat/completions"
        payload = self._payload(model, messages)
        if self.stream:
            return self._call_stream(url, payload)
        try:
            r = requests.post(url, json=payload, headers=self._headers(),
                              timeout=(self.connect_timeout, self.timeout))
        except self._CONN_EXC as e:
            raise ConnectionErrorLLM(f"연결 실패: {type(e).__name__}") from e
        if r.status_code != 200:
            raise _classify_http(r.status_code, _err_message(r))   # 모델오류 vs 서버오류 분류(응답 message 노출)
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ServerError(f"응답 파싱 실패: {e} / {str(data)[:200]}")

    def _call_stream(self, url, payload):
        parts = []
        start = time.time()
        try:
            with requests.post(url, json=payload, headers=self._headers(),
                               timeout=(self.connect_timeout, self.timeout), stream=True) as r:
                if r.status_code != 200:
                    raise _classify_http(r.status_code, _err_message(r))
                for line in r.iter_lines(decode_unicode=True):
                    if SHUTDOWN.is_set():                          # 종료 신호 → 즉시 스트림 중단
                        break
                    if time.time() - start > self.wall_timeout:   # 총 벽시계 상한 → 중단(부분 보존)
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
                    ch = (obj.get("choices") or [{}])[0]
                    delta = ch.get("delta") or {}
                    tok = delta.get("content")
                    if tok:
                        parts.append(tok)
        except self._CONN_EXC as e:
            raise ConnectionErrorLLM(f"연결/스트림 실패: {type(e).__name__}") from e
        if not parts:
            raise ServerError("스트림 응답이 비어있음")   # 서버 응답O인데 생성 0 → 재시작 후보
        return "".join(parts)

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
            text, _ = strip_degenerate(self._call(model, build(prompt)))  # 반복 붕괴 제거
            return _validate(text)

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
                    model = self.fallback_model      # 모델명 오류: 폴백 교체(한도 미차감)
                    continue
                raise SummaryError(" / ".join(reasons))
            except ConnectionErrorLLM as e:
                reasons.append(f"[conn] {e}")
                if retries > 0 and not SHUTDOWN.is_set():
                    retries -= 1
                    _interruptible_sleep(config.LLM_RETRY_WAIT_SEC)  # 대기 후 같은 요청 재시도
                    continue
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
                    continue
                raise SummaryError(" / ".join(reasons))

    def summarize(self, title, content_html, ocr_text=None, image_urls=None):
        """요약. 텍스트 프롬프트 하나에 (있으면) 이미지를 함께 첨부해 넘긴다(비전 전용 프롬프트 X).
        본문·OCR·이미지가 모두 없을 때만 EmptyContentError. engine은 이미지 첨부 시 'vision:<model>×N'."""
        self.ensure_model()
        content_body = html_to_text(content_html)
        has_text = bool((content_body + (ocr_text or "")).strip())
        if not has_text and not image_urls:
            raise EmptyContentError("본문·OCR·이미지 모두 없음")
        body = content_body + (f"\n\n[이미지 OCR (오탈자 가능)]\n{ocr_text}" if ocr_text else "")
        base = self._compose_prompt(title, body)
        if image_urls:
            base += config.LLM_VISION_HINT      # 이미지 있을 때만 '포스터를 읽어라' 지시 추가
        text, model = self._generate(base, image_urls=image_urls or None)
        engine = f"vision:{model}×{len(image_urls)}" if image_urls else model
        return text, engine


class ClovaSummarizer:
    """요약 실패건 한정 외부 폴백. 자격증명 없으면 사용 불가."""

    def __init__(self):
        self.enabled = config.CLOVA_ENABLE

    def summarize(self, title, content_html, ocr_text=None):
        if not self.enabled:
            raise SummaryError("Clova 비활성화")
        # TODO: sauron ClovaSummary.py 이식(자격증명·엔드포인트). 현재는 자리만.
        raise SummaryError("Clova 미구현(자리만 확보)")


def default_summarizer():
    return OpenAICompatSummarizer()
