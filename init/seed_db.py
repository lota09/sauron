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
- 여러 번 실행해도 seen_notices/notices/subscriptions 등 운영 데이터는 그대로.

개발=Windows x86, 타겟=ARM(chroot/proot). 표준 라이브러리만 사용.
"""
import argparse, csv, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCHEMA = os.path.join(ROOT, "db", "schema.sql")

# 시드에서 갱신할 설정 컬럼(채널/역할/active/seeded_at 제외)
CONFIG_COLS = ["name_ko", "kind", "college", "department", "major", "list_url",
               "link_selector", "content_selector", "url_prefix",
               "fetch_type", "login", "seed_pages", "icon_url", "note"]
ALL_COLS = ["dept_id"] + CONFIG_COLS + ["discord_channel_id", "discord_role_id", "active", "seeded_at"]

# NULL 허용 컬럼(빈 문자열 → NULL). 나머지 NOT NULL 컬럼은 원값 유지.
NULLABLE = {"college", "department", "major", "link_selector",
            "content_selector", "icon_url", "note"}
INT_COLS = {"login", "seed_pages"}


def _blank_to_none(v):
    return None if (v is None or str(v).strip() == "") else v


def _coerce(col, v):
    if col in INT_COLS:
        return int(v) if str(v).strip() != "" else (0 if col == "login" else 3)
    if col in NULLABLE:
        return _blank_to_none(v)
    return v  # NOT NULL 텍스트(url_prefix 등)는 '' 그대로 유지


def seed(db_path: str, seed_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON;")

    with open(SCHEMA, encoding="utf-8") as f:
        con.executescript(f.read())

    with open(seed_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

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
        sys.exit(f"seed csv not found: {a.seed} (먼저 generate_seed.py 실행)")
    seed(a.db, a.seed)


if __name__ == "__main__":
    main()
