#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db/migrate_v2.py — 기존 notice.db 를 schema v1 → v2 로 이관.

바뀌는 것:
  1) depts.kind 컬럼 추가('general'|'major'|'etc'). 봇 3단계 구독의 기준.
  2) 자식 FK(seen_notices/notices/subscriptions)에 ON UPDATE CASCADE 부여
     → 앞으로 dept_id(PK) 개명이 부모 UPDATE 한 줄로 자식까지 자동 전파.
  3) scatch 포털 공통 8종 dept_id 개명: usaint→scatch_haksa, portal_*→scatch_*.
  4) kind 값 채우기: scatch_*=general / college 있으면 major / 나머지 etc.

멱등(idempotent): app_meta.schema_version 이 이미 '2' 이상이면 아무 것도 안 함.
백업 권장: 실행 전 notice.db 사본을 떠 두세요.

  python db/migrate_v2.py [--db db/notice.db]
"""
import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

RENAME = {
    "usaint": "scatch_haksa",
    "portal_janghak": "scatch_janghak",
    "portal_chaeyong": "scatch_chaeyong",
    "portal_bongsa": "scatch_bongsa",
    "portal_gukje": "scatch_gukje",
    "portal_foreign": "scatch_foreign",
    "portal_event": "scatch_event",
    "portal_etc": "scatch_etc",
}

# v2 자식 테이블 DDL(= schema.sql 과 동일, ON UPDATE CASCADE 포함)
DDL_SEEN = """
CREATE TABLE seen_notices (
  dept_id       TEXT NOT NULL REFERENCES depts(dept_id) ON DELETE CASCADE ON UPDATE CASCADE,
  url           TEXT NOT NULL,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (dept_id, url)
);"""
DDL_NOTICES = """
CREATE TABLE notices (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id            TEXT NOT NULL REFERENCES depts(dept_id) ON DELETE CASCADE ON UPDATE CASCADE,
  title              TEXT NOT NULL,
  url                TEXT NOT NULL UNIQUE,
  content_raw        TEXT,
  images_json        TEXT,
  ocr_text           TEXT,
  summary            TEXT,
  summary_engine     TEXT,
  status             TEXT NOT NULL DEFAULT 'detected',
  discord_channel_id TEXT,
  discord_message_id TEXT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at         TEXT
);"""
DDL_SUBS = """
CREATE TABLE subscriptions (
  discord_user_id TEXT NOT NULL REFERENCES users(discord_user_id) ON DELETE CASCADE,
  dept_id         TEXT NOT NULL REFERENCES depts(dept_id) ON DELETE CASCADE ON UPDATE CASCADE,
  subscribed_at   TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (discord_user_id, dept_id)
);"""


def _cols(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def _recreate(con, table, ddl, index_sql):
    """table 을 ddl 로 재생성하며 데이터 보존. (FK OFF 상태에서 호출)"""
    cols = _cols(con, table)
    collist = ", ".join(cols)
    con.execute(f"ALTER TABLE {table} RENAME TO {table}_old_v1;")
    con.executescript(ddl)
    con.execute(f"INSERT INTO {table} ({collist}) SELECT {collist} FROM {table}_old_v1;")
    con.execute(f"DROP TABLE {table}_old_v1;")
    for s in index_sql:
        con.execute(s)


def migrate(db_path: str) -> None:
    if not os.path.exists(db_path):
        sys.exit(f"DB 없음: {db_path} (먼저 seed_db.py 로 생성)")
    con = sqlite3.connect(db_path)
    try:
        ver = con.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        ver = None
    cur_ver = int(ver[0]) if ver else 1
    if cur_ver >= 2:
        print(f"[migrate_v2] 이미 v{cur_ver} — 건너뜀(멱등).")
        con.close()
        return

    con.execute("PRAGMA foreign_keys=OFF;")
    con.execute("BEGIN;")
    try:
        # 1) kind 컬럼
        if "kind" not in _cols(con, "depts"):
            con.execute("ALTER TABLE depts ADD COLUMN kind TEXT NOT NULL DEFAULT 'major';")

        # 2) 자식 테이블 ON UPDATE CASCADE 재생성(데이터 보존)
        _recreate(con, "seen_notices", DDL_SEEN, [])
        _recreate(con, "notices", DDL_NOTICES, [
            "CREATE INDEX IF NOT EXISTS idx_notices_status ON notices(status);",
            "CREATE INDEX IF NOT EXISTS idx_notices_dept   ON notices(dept_id);",
        ])
        _recreate(con, "subscriptions", DDL_SUBS, [
            "CREATE INDEX IF NOT EXISTS idx_subs_dept ON subscriptions(dept_id);",
        ])

        # 3) 포털 8종 개명(부모+자식 모두. FK OFF 라 수동 전파)
        renamed = []
        for old, new in RENAME.items():
            if con.execute("SELECT 1 FROM depts WHERE dept_id=?", (old,)).fetchone():
                if con.execute("SELECT 1 FROM depts WHERE dept_id=?", (new,)).fetchone():
                    print(f"[migrate_v2] 경고: {new} 이미 존재 → {old} 개명 건너뜀")
                    continue
                for t in ("depts", "seen_notices", "notices", "subscriptions"):
                    con.execute(f"UPDATE {t} SET dept_id=? WHERE dept_id=?", (new, old))
                renamed.append(f"{old}→{new}")

        # 4) kind 채우기(개명 후 기준)
        con.execute("UPDATE depts SET kind='general' WHERE dept_id LIKE 'scatch\\_%' ESCAPE '\\';")
        con.execute("UPDATE depts SET kind='major' WHERE kind<>'general' "
                    "AND college IS NOT NULL AND TRIM(college)<>'';")
        con.execute("UPDATE depts SET kind='etc' WHERE kind<>'general' "
                    "AND (college IS NULL OR TRIM(college)='');")

        con.execute("UPDATE app_meta SET value='2' WHERE key='schema_version';")
        con.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES('schema_version','2');")
        con.execute("COMMIT;")
    except Exception:
        con.execute("ROLLBACK;")
        con.close()
        raise

    con.execute("PRAGMA foreign_keys=ON;")
    bad = con.execute("PRAGMA foreign_key_check;").fetchall()
    if bad:
        con.close()
        sys.exit(f"[migrate_v2] FK 검증 실패! {bad}")

    from collections import Counter
    kc = Counter(r[0] for r in con.execute("SELECT kind FROM depts").fetchall())
    con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    con.close()
    print(f"[migrate_v2] 완료 · v1→v2")
    print(f"[migrate_v2] 개명 {len(renamed)}건: {', '.join(renamed) or '없음'}")
    print(f"[migrate_v2] kind 분포: {dict(kc)}")
    print(f"[migrate_v2] FK 무결성 OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "db", "notice.db"))
    a = ap.parse_args()
    migrate(a.db)


if __name__ == "__main__":
    main()
