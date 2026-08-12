#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init/drop_dept.py <dept_id> [dept_id ...] — 특정 dept와 그 공지를 라이브 DB에서 제거(멱등).

CSV에서 행을 지우거나 학과를 합쳐도 seed_db는 upsert만 하므로, '이미 시딩된' 옛 dept는
그대로 남는다(중복 크롤/발송 원인). 이 스크립트로 라이브 DB에서 직접 제거한다.
  예) 합치기: python init/drop_dept.py infocom_IT융합전공
      (drop_ssuconvergence.py 의 범용판 — 앞으로는 이걸 쓰면 된다)

실행(★ notice.db 있는 venv):  python init/drop_dept.py <dept_id> ...
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config


def main():
    if len(sys.argv) < 2:
        print("사용법: python init/drop_dept.py <dept_id> [dept_id ...]")
        sys.exit(1)
    con = sqlite3.connect(config.DB_PATH)
    try:
        for did in sys.argv[1:]:
            n1 = con.execute("DELETE FROM notices WHERE dept_id=?", (did,)).rowcount
            n2 = con.execute("DELETE FROM depts   WHERE dept_id=?", (did,)).rowcount
            print(f"[drop] {did}: depts {n2}행 / notices {n1}행 삭제")
        con.commit()
    finally:
        con.close()
    print("[drop] 다음: python init/seed_db.py (남은 설정 반영)")


if __name__ == "__main__":
    main()
