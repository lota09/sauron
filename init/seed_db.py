#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init/seed_db.py  —  DB 초기화 + 학과 시드 (idempotent)

  python init/seed_db.py [--db db/notice.db] [--seed init/depts_seed.csv]

- schema.sql 실행(존재하는 테이블은 IF NOT EXISTS로 보존)
- depts_seed.csv를 upsert
    * 크롤 설정 컬럼(name/selector/fetch_type 등)은 갱신
    * discord_channel_id / role_id 는 시드가 비어있으면 기존값 보존
      (채널 자동생성 단계에서 채워진 값을 덮어쓰지 않음)
    * active / seeded_at 는 운영 중 변경분 보존(건드리지 않음)
- 여러 번 실행해도 notices/subscriptions 등 운영 데이터는 그대로.

개발=Windows x86, 타겟=ARM(chroot/proot). 표준 라이브러리만 사용.
"""
import argparse, csv, json, os, re, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
SCHEMA = os.path.join(ROOT, "db", "schema.sql")

# 시드에서 갱신할 설정 컬럼(채널/역할/active/seeded_at 제외)
CONFIG_COLS = ["name_ko", "kind", "college", "department", "major", "list_url",
               "link_selector", "content_selector", "url_prefix",
               "fetch_type", "fetch_config", "login", "seed_pages", "icon_url", "note"]
ALL_COLS = ["dept_id"] + CONFIG_COLS + ["discord_channel_id", "discord_role_id", "active", "seeded_at"]

# NULL 허용 컬럼(빈 문자열 → NULL). 나머지 NOT NULL 컬럼은 원값 유지.
NULLABLE = {"college", "department", "major", "link_selector",
            "content_selector", "fetch_config", "icon_url", "note"}
INT_COLS = {"login", "seed_pages"}


def _blank_to_none(v):
    return None if (v is None or str(v).strip() == "") else v


def _coerce(col, v):
    if col in INT_COLS:
        return int(v) if str(v).strip() != "" else (0 if col == "login" else 3)
    if col in NULLABLE:
        return _blank_to_none(v)
    return v  # NOT NULL 텍스트(url_prefix 등)는 '' 그대로 유지


FETCH_TYPES = {"html", "json_api"}          # 내장 수집 방식. 그 외 이름 = plugins/<이름>.py 수집 플러그인
JSON_API_REQUIRED = ("list_url", "list_path", "id_key", "title_key", "url_template")
KINDS = {"general", "major", "etc"}


def validate(rows):
    """CSV를 DB에 넣기 전에 검사. 틀린 행을 전부 모아 한 번에 보고한다(크롤 중에 터지지 않게).
    다른 학교가 depts_seed.csv만 채우고 deploy.py를 돌렸을 때, 여기서 걸러져야 한다."""
    errs, seen = [], set()
    for n, r in enumerate(rows, start=2):                     # 2 = 헤더 다음 줄
        did = (r.get("dept_id") or "").strip()
        where = f"{n}행({did or '?'})"
        if not did:
            errs.append(f"{where}: dept_id 비어 있음"); continue
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", did):
            errs.append(f"{where}: dept_id는 영문·숫자·_- 만 (지금: {did!r})")
        if did in seen:
            errs.append(f"{where}: dept_id 중복")
        seen.add(did)
        if not (r.get("name_ko") or "").strip():
            errs.append(f"{where}: name_ko 비어 있음")
        if (r.get("kind") or "major") not in KINDS:
            errs.append(f"{where}: kind는 {sorted(KINDS)} 중 하나 (지금: {r.get('kind')!r})")
        ft = (r.get("fetch_type") or "html").strip()
        if ft not in FETCH_TYPES:
            from core import plugin              # 내장이 아니면 수집 플러그인이어야 한다
            if not plugin.exists(ft):
                errs.append(f"{where}: fetch_type {ft!r} — 내장({sorted(FETCH_TYPES)})도 아니고 plugins/{ft}.py 도 없음")
            else:
                try:
                    miss = plugin.missing_secrets(ft, plugin.load_class(ft, "source"))
                    if miss:
                        errs.append(f"{where}: secrets/plugin_{ft}.json 에 {', '.join(miss)} 필요 "
                                    f"(`python3 deploy.py`가 물어서 채움)")
                except Exception as e:
                    errs.append(f"{where}: plugins/{ft}.py 불러오기 실패: {type(e).__name__}: {e}")
        cfg = {}
        if (r.get("fetch_config") or "").strip():
            try:
                cfg = json.loads(r["fetch_config"])
                if not isinstance(cfg, dict):
                    raise ValueError("객체({...})가 아님")
            except ValueError as e:
                errs.append(f"{where}: fetch_config JSON 오류: {e}")
        if ft == "html":
            if not (r.get("link_selector") or "").strip():
                errs.append(f"{where}: html은 link_selector 필수")
            if not (r.get("list_url") or "").startswith(("http://", "https://")):
                errs.append(f"{where}: list_url이 http(s) 주소가 아님")
            if cfg.get("link_attr") and not cfg.get("url_template"):
                errs.append(f"{where}: link_attr를 쓰면 url_template도 필요")
        elif ft == "json_api":
            miss = [k for k in JSON_API_REQUIRED if k not in cfg]
            if miss:
                errs.append(f"{where}: json_api fetch_config에 {miss} 필요")
            if not (cfg.get("content_key") or cfg.get("detail_path")):
                errs.append(f"{where}: json_api는 content_key(목록에 본문) 또는 detail_path(상세 JSON) 중 하나 필요")
    return errs


def seed(db_path: str, seed_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON;")

    with open(SCHEMA, encoding="utf-8") as f:
        con.executescript(f.read())

    # 기존 DB 이관: CREATE IF NOT EXISTS는 컬럼 추가를 못하므로 누락 컬럼은 ALTER로 보강(멱등).
    have = {r[1] for r in con.execute("PRAGMA table_info(depts)").fetchall()}
    if "fetch_config" not in have:
        con.execute("ALTER TABLE depts ADD COLUMN fetch_config TEXT")
        print("[seed_db] depts.fetch_config 컬럼 추가(v5 이관)")

    with open(seed_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    errs = validate(rows)
    if errs:
        con.close()
        print(f"[seed_db] ✗ {seed_path} 에 문제 {len(errs)}건 — DB는 건드리지 않았습니다:")
        for e in errs:
            print(f"    - {e}")
        sys.exit(1)

    set_clause = ", ".join(f"{c}=excluded.{c}" for c in CONFIG_COLS)
    sql = f"""
        INSERT INTO depts
          (dept_id, {", ".join(CONFIG_COLS)}, discord_channel_id, discord_role_id, active, seeded_at)
        VALUES (?, {", ".join(["?"] * len(CONFIG_COLS))}, ?, ?, ?, NULL)
        ON CONFLICT(dept_id) DO UPDATE SET
          {set_clause},
          -- 시드가 비었으면 기존 채널/역할 보존 (자동생성분 보호)
          discord_channel_id = COALESCE(NULLIF(excluded.discord_channel_id, ''), depts.discord_channel_id),
          discord_role_id    = COALESCE(NULLIF(excluded.discord_role_id, ''),    depts.discord_role_id)
          -- active, seeded_at 는 의도적으로 미갱신(운영 상태 보존)
    """
    inserted = updated = 0
    for r in rows:
        exists = con.execute("SELECT 1 FROM depts WHERE dept_id=?", (r["dept_id"],)).fetchone()
        params = [r["dept_id"]]
        params += [_coerce(c, r.get(c)) for c in CONFIG_COLS]
        # 신규 삽입 시엔 채널/역할도 시드값(있으면) 사용
        params += [_blank_to_none(r.get("discord_channel_id")),
                   _blank_to_none(r.get("discord_role_id")),
                   int(r.get("active") or 1)]
        con.execute(sql, params)
        if exists:
            updated += 1
        else:
            inserted += 1

    con.commit()

    # 요약
    total = con.execute("SELECT COUNT(*) FROM depts").fetchone()[0]
    with_ch = con.execute("SELECT COUNT(*) FROM depts WHERE discord_channel_id IS NOT NULL").fetchone()[0]
    ftypes = con.execute("SELECT fetch_type, COUNT(*) FROM depts GROUP BY fetch_type ORDER BY 2 DESC").fetchall()
    con.close()

    print(f"[seed_db] DB={db_path}")
    print(f"[seed_db] 신규 {inserted} / 갱신 {updated} / 총 {total} 학과")
    print(f"[seed_db] 채널ID 보유 {with_ch}")
    print(f"[seed_db] fetch_type {dict(ftypes)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "db", "notice.db"))
    ap.add_argument("--seed", default=os.path.join(HERE, "depts_seed.csv"))
    a = ap.parse_args()
    if not os.path.exists(a.seed):
        sys.exit(f"seed csv not found: {a.seed} (학교 사이트 목록을 이 CSV로 작성 — README '새 학교에 도입')")
    seed(a.db, a.seed)


if __name__ == "__main__":
    main()
