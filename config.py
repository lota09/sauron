# -*- coding: utf-8 -*-
"""
config.py — 런타임 설정. 환경변수 > secrets/config.json > 기본값 순으로 오버라이드.
개발(Windows x86)과 타겟(ARM chroot/proot)에서 값만 바꿔 동작.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_overlay():
    path = os.path.join(_HERE, "secrets", "config.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


_OV = _load_overlay()


def _get(key, default):
    if key in os.environ:
        return os.environ[key]
    if key in _OV:
        return _OV[key]
    return default


def _get_int(key, default):
    try:
        return int(_get(key, default))
    except (TypeError, ValueError):
        return default


def _get_bool(key, default):
    v = _get(key, None)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# ── 경로 ─────────────────────────────────────────────
DB_PATH = _get("SAURON_DB", os.path.join(_HERE, "db", "notice.db"))
SEED_CSV = _get("SAURON_SEED", os.path.join(_HERE, "init", "depts_seed.csv"))

# ── 요약 LLM (OpenAI 호환) ───────────────────────────
# 개발: http://192.168.50.153:8000/v1 · 배포(Note20): http://localhost:8000/v1
LLM_BASE_URL = _get("LLM_BASE_URL", "http://192.168.50.153:8000/v1")
LLM_API_KEY = _get("LLM_API_KEY", "sk-none")          # 로컬 런타임은 보통 불필요
# 'auto'(기본) = 시작 시 서버에 로드된 모델을 1회 조회해 자동 확정(/health → /v1/models).
#   특정 모델을 강제하려면 정확한 이름을 넣는다. 확인: curl http://192.168.50.153:8000/v1/models
LLM_MODEL = _get("LLM_MODEL", "auto")
LLM_MODEL_FALLBACK = _get("LLM_MODEL_FALLBACK", "")    # 품질 미달 시 승격. 빈값=미사용
# 타임아웃(초): connect=서버 연결 대기, read=스트리밍 중 바이트 간 최대 침묵, wall=한 요약 총 상한.
# 이 기기는 GPU라 첫 토큰 5~15초 → read 20이면 충분. 서버 다운 시 connect 10초로 빠르게 실패.
LLM_CONNECT_TIMEOUT = _get_int("LLM_CONNECT_TIMEOUT", 10)
LLM_TIMEOUT = _get_int("LLM_TIMEOUT", 20)              # read timeout
LLM_WALL_TIMEOUT = _get_int("LLM_WALL_TIMEOUT", 120)   # 한 요약 총 벽시계 상한(반복폭주 방지)
LLM_MAX_CONCURRENCY = _get_int("LLM_MAX_CONCURRENCY", 1)  # 폰=단일슬롯. 확장 시 상향
LLM_MAX_INPUT_CHARS = _get_int("LLM_MAX_INPUT_CHARS", 6000)  # 초과 시 선축소/절단
LLM_STREAM = _get_bool("LLM_STREAM", True)             # litertlm 서버는 스트리밍 사용(검증된 형태)
LLM_MAX_TOKENS = _get_int("LLM_MAX_TOKENS", 2048)      # 출력 상한. 잘리면 상향
LLM_TEMPERATURE = _get("LLM_TEMPERATURE", "")          # 빈값=미전송(Gemma/litertlm 호환)
LLM_FREQUENCY_PENALTY = _get("LLM_FREQUENCY_PENALTY", "")  # ⚠ OlliteRT는 무시함(temperature/top_k/top_p/max_tokens만 반영). 타 백엔드용 훅. 반복은 strip_degenerate로 잡음.

# 언어 이탈 검사(결정론) — 2B judge가 못 잡는 외국어 혼입/붕괴를 코드로 차단.
LLM_ENFORCE_KOREAN = _get_bool("LLM_ENFORCE_KOREAN", True)
LLM_MAX_HAN = _get_int("LLM_MAX_HAN", 5)                    # 한자(CJK) 이보다 많으면 중국어 주입 의심
LLM_MIN_HANGUL_RATIO = float(_get("LLM_MIN_HANGUL_RATIO", "0.15"))  # 한글/(한글+영문) 최소비(영어 많은 정상요약은 통과)

# ── 요약 프롬프트 (요약 스타일 조정 지점 — 여기만 고치면 됨) ─────────
# Gemma엔 system 턴이 없어 두 값을 하나의 user 메시지로 합쳐 보냄.
LLM_SYSTEM_PROMPT = (
    "너는 대학 학사공지를 학생에게 전달하는 요약 비서다. "
    "한국어로만, 개조식(불릿)으로 대충 어떤 내용인지만 짧게 전달한다. "
    "거절·머리말·맺음말 없이 요약 본문만 출력한다."
)
LLM_USER_TEMPLATE = """다음 학사공지를 개조식 불릿으로 요약해줘.
- 각 항목은 '- '로 시작하는 한 줄. 하위 불릿(들여쓰기)은 쓰지 말고 최상위 불릿만.
- 문장은 '4학년 대상', '신청 가능'처럼 종결어미 생략 또는 '~함/~임/~바람'.
- 분량은 공지 정보량에 따라 2줄에서 6줄 사이를 유지. 핵심만 추리고 세부 조건·목록을 전부 나열하지 말 것.
- 마감일·대상·신청방법을 우선 포함. 장소·주의사항은 정말 중요할 때만.
- 인사말·설명·머리말 없이 불릿만 출력.

[제목]
{title}

[본문]
{body}"""

# ── Clova 폴백(요약 실패건 한정) ─────────────────────
CLOVA_ENABLE = _get_bool("CLOVA_ENABLE", False)

# ── OCR ──────────────────────────────────────────────
OCR_BACKEND = _get("OCR_BACKEND", "none")   # 'tesseract' | 'paddle' | 'none'
OCR_LANG = _get("OCR_LANG", "kor")
OCR_TIMEOUT = _get_int("OCR_TIMEOUT", 60)

# ── 크롤 ─────────────────────────────────────────────
UPDATE_LIMIT = _get_int("UPDATE_LIMIT", 5)      # 신규가 이보다 많으면 사이트깨짐 의심→대량알림 차단
SEED_PAGES = _get_int("SEED_PAGES", 3)          # depts.seed_pages 없을 때 기본
REQUEST_TIMEOUT = _get_int("REQUEST_TIMEOUT", 30)
CRAWL_INTERVAL_SEC = _get_int("CRAWL_INTERVAL_SEC", 600)  # 10분
USER_AGENT = _get("USER_AGENT",
                  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
INFOCOM_RETRY = _get_int("INFOCOM_RETRY", 3)    # 학교서버 버그 F5 흉내 재시도 횟수

# ── 디스코드 ─────────────────────────────────────────
DISCORD_TOKEN_FILE = _get("DISCORD_TOKEN_FILE", os.path.join(_HERE, "secrets", "discord-api-info.json"))
DISCORD_DEBUG_CHANNEL_ID = _get("DISCORD_DEBUG_CHANNEL_ID", "1355610759777882162")  # 운영 감시채널
# 길드(서버) ID도 debug/prod로 분기. DEBUG(또는 debug 플래그)=디버깅 서버, 아니면 실서비스 서버.
DEBUG_GUILD_ID = _get("DEBUG_GUILD_ID", "1195291355258310696")   # 디버깅 서버
PROD_GUILD_ID = _get("PROD_GUILD_ID", "")                         # 실서비스 서버(최종 검수 후 입력)
DISCORD_CHANNEL_PREFIX = _get("DISCORD_CHANNEL_PREFIX", "")  # 학과 채널명 접두(선택)

# DEBUG 모드: True면 실제 학과채널 대신 아래 '가짜 개발 채널'로 전송(개발 중 실서비스 방해 X).
#   전송 라우팅 우선순위:  --dryrun(전송안함) > DEBUG_EN(가짜채널) > 실제 학과채널
#   실행 시 '--debug'/'--prod'/'debug 명령'이 config.DEBUG_EN 을 확정(main.py). 이후 모두 이 단일 식별자를 봄.
DEBUG_NOTICE_CHANNEL_ID = _get("DEBUG_NOTICE_CHANNEL_ID", "1530319308331155486")     # 통합공지(모든 학과 몰빵)
DEBUG_SUBSCRIBE_CHANNEL_ID = _get("DEBUG_SUBSCRIBE_CHANNEL_ID", "1530318804968538195")  # 구독관리
DEBUG_DEBUG_CHANNEL_ID = _get("DEBUG_DEBUG_CHANNEL_ID", "1355520933598859365")       # 디버그(개발용)
# 디버그 모드 단일 식별자. 라우팅은 config가 아니라 '실행 플래그'로만 결정(안전).
# 기본 True(가짜 개발채널). 오직 '--prod' 만 False(실채널). config.json은 이 값을 못 건드림.
DEBUG_EN = True
# dry-run(디스코드 전송 안 함)은 config가 아니라 '--dryrun' 플래그로만. 기본은 전송함.

ICON_DEFAULT = _get("ICON_DEFAULT", "https://ssu.ac.kr/wp-content/uploads/2019/05/suu_emblem1.jpg")


def active_guild_id(debug=None):
    """debug=True(또는 DEBUG_EN)면 디버깅 서버, 아니면 실서비스 서버 ID 반환."""
    d = DEBUG_EN if debug is None else debug
    return DEBUG_GUILD_ID if d else PROD_GUILD_ID


def debug_from_argv(argv):
    """스크립트용 debug 판정: --prod면 False, --debug면 True, 아니면 DEBUG_EN."""
    if "--prod" in argv:
        return False
    if "--debug" in argv:
        return True
    return DEBUG_EN
