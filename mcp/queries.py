# -*- coding: utf-8 -*-
"""
mcp/queries.py — sauron DB 읽기 전용 쿼리 (= 크롤러와 MCP 사이의 'DB 계약').

MCP 서버는 사우론 코드를 import하지 않는다. 크롤러를 어떻게 나누거나 고쳐도 이 파일이 읽는
열·상태값·app_meta 키만 유지되면 MCP는 그대로 돈다. 읽는 것 전부가 아래 상수에 모여 있다.
  · depts   : dept_id·name_ko·kind·college·active (디스코드 채널·역할 ID는 읽지 않는다)
  · notices : NOTICE_COLS + content_raw·images_json (full 상세용)
  · app_meta: run_heartbeat · crawl_fail:<dept_id> 만 (채널 ID 등 다른 키는 읽지 않는다)

출력 원칙: '설명은 충분히, 출력은 적게'. 출력은 대화에 누적되므로 고정 정보(면책 문구·학과 이름·
시간대)는 도구 설명으로 보내고, 출력에는 공지마다 다른 값과 '이상이 있을 때의 신선도'만 남긴다.

DB는 mode=ro + query_only 로 연다 → 이 모듈로는 DB를 바꿀 수 없다. 호출마다 새로 연결한다
(SQLite 연결은 1ms 미만이고, 크롤러가 DB를 갈아끼워도 다음 호출부터 새 파일을 본다).
"""
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from itertools import groupby

KST = timezone(timedelta(hours=9))

NOTICE_COLS = "n.id, n.dept_id, n.title, n.url, n.summary, n.status, n.first_seen_at, n.created_at"
# 발견 시각: 처리(promote)된 공지는 created_at, 시딩만 된 공지는 first_seen_at
DETECTED = "COALESCE(n.created_at, n.first_seen_at)"

META_HEARTBEAT = "run_heartbeat"
META_FAIL_PREFIX = "crawl_fail:"

# 상세도 — 호출하는 AI가 토큰과 정확도를 저울질해 고른다.
#   brief   : id·학과·제목·발견시각 (목록 훑기. url도 뺀다 — 일부 사이트 url이 수백 자)
#   summary : + AI 요약·원문 url
#   full    : + 본문 발췌·이미지 개수
DETAILS = ("brief", "summary", "full")
MAX_LIMIT = {"brief": 50, "summary": 30, "full": 10}
MAX_DAYS = 365
MAX_QUERY = 100
EXCERPT_CHARS = 1500

KIND_LABEL = {"general": "전교", "etc": "기타"}   # major는 단과대 이름으로 묶는다


def connect(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    return conn


def _clamp(v, lo, hi, default):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _kst(utc_text):
    """'YYYY-MM-DD HH:MM:SS'(SQLite datetime('now'), UTC) → 'YYYY-MM-DD HH:MM'(KST)."""
    if not utc_text:
        return None
    try:
        dt = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return utc_text
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")


def _since_utc(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _html_to_text(html):
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def _images_count(images_json):
    try:
        return len(json.loads(images_json or "[]"))
    except (TypeError, ValueError):
        return 0


def _notice(row, detail):
    out = {"id": row["id"], "dept": row["dept_id"], "title": row["title"]}
    if row["status"] == "seeded":
        # 사우론 시작·게시판 추가 시점에 제목만 기록된 과거 공지 — first_seen_at은 기록 시각이지 게시 시각이 아니다
        out["at"] = None
        out["seeded"] = True
    else:
        out["at"] = _kst(row["created_at"] or row["first_seen_at"])
    if detail == "brief":
        return out
    out["summary"] = row["summary"]          # None = 요약 없음(실패·제목만 기록된 공지)
    out["url"] = row["url"]
    if detail == "full":
        text = _html_to_text(row["content_raw"])
        out["content"] = text[:EXCERPT_CHARS] or None
        if len(text) > EXCERPT_CHARS:
            out["content_truncated"] = True
        # 이미지 url은 내지 않는다(사이트 아이콘이 섞이고 토큰만 든다). 개수만 — 포스터에만 정보가 있을 수 있다는 신호
        n = _images_count(row["images_json"])
        if n:
            out["images"] = n
    return out


# ── 학과 목록(도구 설명에 넣는다) ─────────────────────────────────────────────

def active_dept_ids(conn):
    return {r[0] for r in conn.execute("SELECT dept_id FROM depts WHERE active = 1")}


def dept_catalog(conn):
    """'IT대학: cse=컴퓨터학부, sw=소프트웨어학부, …' 줄 모음. 서버 시작 시 한 번 만들어 설명에 넣는다."""
    rows = conn.execute("SELECT dept_id, name_ko, kind, COALESCE(college, '') AS college FROM depts "
                        "WHERE active = 1 ORDER BY kind = 'major', kind, college, name_ko").fetchall()
    lines = []
    for (kind, college), group in groupby(rows, key=lambda r: (r["kind"], r["college"])):
        head = KIND_LABEL.get(kind) or college or "기타"
        items = []
        for r in group:
            name = r["name_ko"]
            short = name.replace(college, "", 1).strip() if college else name
            items.append(f"{r['dept_id']}={short or name}")
        lines.append(f"{head}: {', '.join(items)}")
    return "\n".join(lines)


# ── 신선도 ───────────────────────────────────────────────────────────────────

def freshness_issues(conn, crawl_interval_sec, dept_ids=None):
    """이상이 있을 때만 dict, 정상이면 None (출력에 아무것도 붙이지 않는다).
    stale: 수집기가 멈춤(heartbeat가 주기의 3배 넘게 오래됨). failing: 조회 대상 중 수집 실패 중인 학과."""
    out = {}
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (META_HEARTBEAT,)).fetchone()
    hb = float(row["value"]) if row and row["value"] else None
    if hb is None or time.time() - hb > 3 * crawl_interval_sec:
        out["stale"] = {"last_crawl": datetime.fromtimestamp(hb, KST).strftime("%Y-%m-%d %H:%M") if hb else None}

    failing = {}
    for r in conn.execute("SELECT key, value FROM app_meta WHERE key LIKE ?", (META_FAIL_PREFIX + "%",)):
        did = r["key"][len(META_FAIL_PREFIX):]
        if dept_ids and did not in dept_ids:
            continue
        try:
            since = json.loads(r["value"]).get("since")
        except (TypeError, ValueError, AttributeError):
            since = None
        failing[did] = datetime.fromtimestamp(since, KST).strftime("%Y-%m-%d %H:%M") if since else None
    if failing:
        out["failing_since"] = failing
    return out or None


# ── 조회 ─────────────────────────────────────────────────────────────────────

def _like(t):
    return "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _nospace(col):
    """공백·줄바꿈을 지운 열 — 'AI반도체'와 'AI 반도체'를 같게 본다."""
    return f"REPLACE(REPLACE(COALESCE({col}, ''), ' ', ''), char(10), '')"


SEARCH_COLS = ("n.title", "n.summary", "n.content_raw")


def _parse_terms(query):
    """'반도체|NPU 채용' → [['반도체','NPU'], ['채용']] : 공백=AND, |=OR. 단어 안의 공백은 없앤 형태로 비교한다."""
    terms = []
    for word in str(query or "")[:MAX_QUERY].split()[:5]:
        alts = [a for a in word.split("|") if a][:5]
        if alts:
            terms.append(alts)
    return terms


def _match(terms, cols):
    """모든 단어(AND)가, 각 단어의 대안 중 하나라도(OR), cols 중 어디엔가 있으면 참."""
    conds, args = [], []
    for alts in terms:
        ors = []
        for a in alts:
            for col in cols:
                ors.append(f"{_nospace(col)} LIKE ? ESCAPE '\\'")
                args.append(_like(a))
        conds.append("(" + " OR ".join(ors) + ")")
    return " AND ".join(conds), args


# 같은 공지가 여러 게시판에 따로 올라온 경우(URL은 게시판마다 다르다) 묶는 기준
DEDUP_MIN_KEY = 12       # 정규화 제목이 이보다 짧으면 묶지 않음("휴강 안내" 같은 흔한 제목 오병합 방지)
DEDUP_WINDOW_DAYS = 14   # 발견 시각이 이만큼 이내일 때만 같은 공지로 본다


def _title_key(title):
    return re.sub(r"[\W_]+", "", title or "").lower()


def _same(k1, k2):
    """정규화 제목(공백·기호 제거)이 같거나, 짧은 쪽이 긴 쪽 안에 통째로 들어 있으면 같은 공지.
    학과마다 앞에 분류명('[장학]', '장학★')·뒤에 꼬리('(~9/9까지)', '홍보 요청')를 붙여 올리는 경우를 잡는다.
    실측(2026-11-01): 포함 관계로 새로 묶이는 56개 조합 모두 같은 공지였다. 비용은 150행에 약 4ms."""
    short, long_ = sorted((k1, k2), key=len)
    return len(short) >= DEDUP_MIN_KEY and short in long_


def _dedup(rows):
    """[(row, notice)] → 대표 notice 목록. 묶인 것은 대표에 also=['dept_id:id', …] (다른 게시판의 사본).
    묶음 안의 '모든' 제목과 비교한다 — 꼬리가 서로 다른 두 제목('…안내(~9/9까지)', '…안내(~2026.09.09)')도
    둘 다 포함하는 제목('…안내')이 묶음에 있으면 같은 묶음으로 들어간다."""
    groups = []                            # [keys(list), times(list), rep notice]
    for row, n in rows:
        key = _title_key(row["title"])
        when = _parse_utc(row["created_at"] or row["first_seen_at"])
        for keys, times, rep in groups:
            if any(_same(key, k) for k in keys) and \
                    min(abs((when - t).days) for t in times) <= DEDUP_WINDOW_DAYS:
                keys.append(key)
                times.append(when)
                if n["dept"] != rep["dept"] and not any(a.startswith(n["dept"] + ":") for a in rep.get("also", ())):
                    rep.setdefault("also", []).append(f"{n['dept']}:{n['id']}")
                break
        else:
            groups.append(([key], [when], n))
    return [rep for _, _, rep in groups]


def _parse_utc(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return datetime.min


def query_notices(conn, query=None, dept_ids=None, days=7, detail="brief", limit=10):
    """query 없음 = 최근 목록(시딩분 제외), 있음 = 검색(시딩분 포함 · 제목→요약→본문 순 정렬).
    여러 게시판에 같은 공지가 있으면 1건으로 묶고 also에 나머지 게시판을 적는다."""
    detail = detail if detail in DETAILS else "brief"
    limit = _clamp(limit, 1, MAX_LIMIT[detail], 10)
    days = _clamp(days, 1, MAX_DAYS, 7)
    terms = _parse_terms(query)

    where, args = [f"{DETECTED} >= ?"], [_since_utc(days)]
    if dept_ids:
        where.append(f"n.dept_id IN ({','.join('?' * len(dept_ids))})")
        args += list(dept_ids)
    rank, rank_args = "0", []
    if terms:
        cond, cond_args = _match(terms, SEARCH_COLS)
        where.append(cond)
        args += cond_args
        # 2=제목에서 전부 맞음, 1=요약에서 전부, 0=본문에서만 → 이 순서로 정렬(같으면 최신순)
        t_cond, t_args = _match(terms, ("n.title",))
        s_cond, s_args = _match(terms, ("n.summary",))
        rank = f"CASE WHEN {t_cond} THEN 2 WHEN {s_cond} THEN 1 ELSE 0 END"
        rank_args = t_args + s_args
    else:
        where.append("n.status != 'seeded'")

    cols = NOTICE_COLS + (", n.content_raw, n.images_json" if detail == "full" else "")
    # 중복을 묶으면 건수가 줄어드므로 넉넉히 읽고 묶은 뒤 자른다
    rows = conn.execute(
        f"SELECT {cols}, {rank} AS rank FROM notices n WHERE {' AND '.join(where)} "
        # 시딩 공지는 같은 순위 안에서 뒤로 — 시각이 '기록한 날'이라 최근 추가한 게시판의 옛 공지가 맨 위로 뜨지 않게
        f"ORDER BY rank DESC, n.status = 'seeded', {DETECTED} DESC, n.id DESC LIMIT ?",
        rank_args + args + [limit * 3]).fetchall()
    pairs = []
    for r in rows:
        n = _notice(r, detail)
        if terms and r["rank"] == 0:
            n["body_match"] = True        # 본문에서만 맞음 — 관련성이 낮을 수 있다
        pairs.append((r, n))
    return _dedup(pairs)[:limit]


MAX_BATCH = 10


def get_notices(conn, notice_ids, detail="full"):
    """여러 건 상세. 반환: (찾은 notice 목록(요청 순서), 없는 id 목록)."""
    detail = "summary" if detail == "summary" else "full"
    ids = []
    for x in notice_ids or []:
        i = _clamp(x, 0, 2**62, 0)
        if i and i not in ids:
            ids.append(i)
    ids = ids[:MAX_BATCH]
    if not ids:
        return [], []
    rows = conn.execute(f"SELECT {NOTICE_COLS}, n.content_raw, n.images_json FROM notices n "
                        f"WHERE n.id IN ({','.join('?' * len(ids))})", ids).fetchall()
    by_id = {r["id"]: _notice(r, detail) for r in rows}
    return [by_id[i] for i in ids if i in by_id], [i for i in ids if i not in by_id]
