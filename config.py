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

# ── 요약 재시도 정책(공지 1건 기준) ──────────────────
#  · 재시도 한도는 공지당 LLM_RETRY_LIMIT회(기본 1). 이를 소진하면 영구 실패(재크롤/재부팅에도 재시도 X).
#  · 모델명 오류: LLM_MODEL_FALLBACK으로 교체 재시도(한도 미차감). 폴백 없으면 그냥 실패.
#  · 연결/타임아웃(서버 무응답): LLM_RETRY_WAIT_SEC초 대기 후 재시도(한도 차감).
#  · 언어이탈/불량응답(검증 실패): 즉시 재시도(한도 차감). greedy=결정론이라 '같은 모델 재시도=동일 실패'이므로
#      폴백 모델이 있으면 폴백으로, 없으면 프롬프트를 한국어 강화로 변형해 재시도(입력을 바꿔야 결과가 바뀜).
LLM_RETRY_LIMIT = _get_int("LLM_RETRY_LIMIT", 1)
LLM_RETRY_WAIT_SEC = _get_int("LLM_RETRY_WAIT_SEC", 5)   # 연결 실패 시 재시도 전 대기(초)

# ── 멀티모달(비전) — 이미지 대체 공지 구제 ─────────────
#  본문·OCR 모두 없고 이미지만 있는 공지(제목O·본문X·그림O)를 비전 LLM으로 요약 시도.
#  ⚠ 2B 비전은 정확도 한계(브랜드/날짜 오독·환각 관측). '없는 것보단 낫다 + 면책문구' 관점의 best-effort.
#  반복붕괴는 strip_degenerate가, 언어이탈은 검증기가 잡고, 실패 시 리롤(프롬프트 미세변형).
# 공지에 이미지가 있으면 요약 요청에 '무조건' 첨부(텍스트 유무·글자수 무관). 프롬프트는 텍스트용과 공유
# — LLM_USER_TEMPLATE 하나로 텍스트+이미지 함께 넘기면 모델이 알아서 이미지를 참고한다(비전 전용 프롬프트 X).
LLM_VISION = _get_bool("LLM_VISION", True)                    # 이미지 첨부 on/off
# ⚠ 다운스케일 트레이드오프(probe 실측): 768~1024는 포스터 날짜를 정독하면서 다중이미지 페이로드를 크게 줄인다
#   (2479px 원본 3장 ~3MB → ~0.5MB). 512는 글자가 뭉개져 '환각 날짜'가 나옴 → 768 미만 금지.
#   조밀한 표까지 정밀 추출이 필요하면 다운스케일이 아니라 타일링/OCR로. Pillow 없으면 이 값 무시(원본 전송).
LLM_VISION_MAX_PX = _get_int("LLM_VISION_MAX_PX", 1024)       # 전송 전 최대 변(px)
LLM_VISION_MAX_IMAGES = _get_int("LLM_VISION_MAX_IMAGES", 4)  # 한 요청 최대 이미지 수(컨텍스트/지연 상한)
# 아이콘·썸네일 등 초소형 이미지는 비전에 안 넣는다(LiteRT 텐서버퍼 크래시·무의미 입력 방지).
#   Pillow 있으면 '최소 변(px)'로, 없으면 바이트 크기로 근사 컷. 실제 포스터는 보통 768px↑이라 200 컷은 안전.
LLM_VISION_MIN_PX = _get_int("LLM_VISION_MIN_PX", 200)
LLM_VISION_MIN_BYTES = _get_int("LLM_VISION_MIN_BYTES", 3000)
# 이미지가 첨부될 때만 프롬프트에 덧붙는 지시(텍스트 전용 요약엔 안 붙어 '이미지' 오해 방지).
LLM_VISION_HINT = _get("LLM_VISION_HINT",
                       "\n- 첨부된 포스터 이미지 안의 날짜·주요일정·대상·표 내용을 읽어 요약에 반영할 것.")

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
- 주요일정·대상·신청방법을 우선 포함. 장소·주의사항은 정말 중요할 때만.
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
JSON_API_SCAN_PAGES = _get_int("JSON_API_SCAN_PAGES", 12)  # json_api 본문 캐시 미스 시 훑을 최대 페이지수(깊은페이지 재처리)
REQUEST_TIMEOUT = _get_int("REQUEST_TIMEOUT", 30)          # read timeout(초): 연결 후 응답 대기
REQUEST_CONNECT_TIMEOUT = _get_int("REQUEST_CONNECT_TIMEOUT", 5)  # connect timeout: 죽은 호스트 빠른 실패(30→5초)
CRAWL_CONCURRENCY = _get_int("CRAWL_CONCURRENCY", 8)       # 목록 fetch 동시 개수: 한 곳이 막혀도 나머지 진행
CRAWL_INTERVAL_SEC = _get_int("CRAWL_INTERVAL_SEC", 600)  # 10분
# 크롤러 생존판정: heartbeat가 이 시간 넘게 갱신 안 되면 '멈춤(stale)'. 기본 = 크롤주기×2(한 사이클 놓쳐도 여유).
RUN_STALE_SEC = _get_int("RUN_STALE_SEC", CRAWL_INTERVAL_SEC * 2)
USER_AGENT = _get("USER_AGENT",
                  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
INFOCOM_RETRY = _get_int("INFOCOM_RETRY", 3)    # 학교서버 버그 F5 흉내 재시도 횟수

# ── 로깅 ───────────────────────────────────────
# 콘솔 + logs/ 에 일자별 회전 파일(자정 롤오버). sauron.sh는 redirect 없이 실행하면 됨.
LOG_DIR = _get("LOG_DIR", os.path.join(_HERE, "logs"))
LOG_BACKUP_DAYS = _get_int("LOG_BACKUP_DAYS", 14)   # 회전 로그 보관 일수

# ── 디스코드 ─────────────────────────────────────────
DISCORD_TOKEN_FILE = _get("DISCORD_TOKEN_FILE", os.path.join(_HERE, "secrets", "discord-api-info.json"))

# 길드(서버) — 첫 세팅에 필요한 건 봇 토큰 + 이 값 하나뿐. setup_guild가 여기에 역할·채널을 만든다.
#   배포 시 다른 서버로 옮기려면 값만 바꿔 setup_guild를 재실행하면 채널ID가 자동 갱신된다.
#   DEBUG_GUILD_ID/PROD_GUILD_ID는 두 서버를 오갈 때만 쓰는 '선택적' 오버라이드(없으면 DISCORD_GUILD_ID 사용).
DISCORD_GUILD_ID = _get("DISCORD_GUILD_ID", "")
DEBUG_GUILD_ID = _get("DEBUG_GUILD_ID", DISCORD_GUILD_ID)
PROD_GUILD_ID = _get("PROD_GUILD_ID", "")
DISCORD_CHANNEL_PREFIX = _get("DISCORD_CHANNEL_PREFIX", "")  # 학과 채널명 접두(선택)

# 통합공지(mono, --dst mono 대상)·감시(디버그) 채널 — 둘 다 setup_guild가 '이름'으로
#   자동 생성/재사용하고 그 채널ID를 DB(app_meta: mono_channel_id·debug_channel_id)에 저장한다.
#   런타임은 build_components가 DB에서 읽어 Notifier에 주입 → 사람이 채널ID를 secrets에 손으로
#   넣지 않는다(넣어도 읽지 않음). 채널 '이름'만 바꾸고 싶으면 아래 두 값을 secrets에서 오버라이드.
MONO_CHANNEL_NAME = _get("MONO_CHANNEL_NAME", "Sauron-Mono")
DEBUG_CHANNEL_NAME = _get("DEBUG_CHANNEL_NAME", "Sauron-Debug")
# mono·debug 채널이 들어가는 카테고리 + 열람 역할(둘 다 sauron 관리). 없으면 setup_guild가 생성.
#   해당 채널은 developers만 열람(@everyone 숨김)·봇/관리자만 전송. 디버그 메시지는 이 역할을 멘션.
DEV_CATEGORY_NAME = _get("DEV_CATEGORY_NAME", "developers")
DEV_ROLE_NAME = _get("DEV_ROLE_NAME", "developers")

# 디버그 모드 단일 식별자. 라우팅은 config가 아니라 '실행 플래그'로만 결정(안전).
DEBUG_EN = True

ICON_DEFAULT = _get("ICON_DEFAULT", "https://ssu.ac.kr/wp-content/uploads/2019/05/suu_emblem1.jpg")


def active_guild_id(debug=None):
    """debug=True(또는 DEBUG_EN)면 디버깅 서버, 아니면 실서비스 서버. 각각 없으면 DISCORD_GUILD_ID로 폴백."""
    d = DEBUG_EN if debug is None else debug
    return (DEBUG_GUILD_ID or DISCORD_GUILD_ID) if d else (PROD_GUILD_ID or DISCORD_GUILD_ID)


def debug_from_argv(argv):
    """스크립트용 debug 판정: --prod면 False, --debug면 True, 아니면 DEBUG_EN."""
    if "--prod" in argv:
        return False
    if "--debug" in argv:
        return True
    return DEBUG_EN
