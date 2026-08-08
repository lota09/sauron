#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init/drop_ssuconvergence.py — 죽은 ssuconvergence dept를 sls로 통합하며 라이브 DB에서 제거(일회성, 멱등).

자유전공학부는 sls(sls.ssu.ac.kr) 행으로 단일화됨. ssuconvergence.co.kr는 폐쇄 도메인이라
그 dept와 (아직 알림 전인) 시드 공지를 삭제한다. 이미 없으면 0건으로 조용히 끝난다.

실행(★ notice.db가 있는 PC/venv):  python init/drop_ssuconvergence.py
※ 일회성. 실행 후 삭제해도 됨.
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config

DEAD = "ssuconvergence"


def main():
    con = sqlite3.connect(config.DB_PATH)
    try:
        n_notices = con.execute("DELETE FROM notices WHERE dept_id=?", (DEAD,)).rowcount
        n_dept = con.execute("DELETE FROM depts WHERE dept_id=?", (DEAD,)).rowcount
        con.commit()
    finally:
        con.close()
    print(f"[drop] {DEAD}: depts {n_dept}행 / notices {n_notices}행 삭제")
    print("[drop] 다음: python init/seed_db.py  (sls 설정 확인 반영)")


if __name__ == "__main__":
    main()
