# -*- coding: utf-8 -*-
"""
experiments/vision_probe/dump_content.py — 공지 상세의 content_selector 요소 HTML을 덤프.

게시일이 <time>으로 마킹돼 있는지, 아니면 별도 메타 요소/평문인지 눈으로 보고
'구조 제거(<time>)로 충분한가 / content_selector(DB)를 좁혀야 하나'를 판단하는 용도.

실행(사이트에 닿는 PC):
  python experiments/vision_probe/dump_content.py "<공지 URL>" [dept_id]
"""
import os
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import config
from db.store import Store
from crawl.fetcher import Fetcher


def main():
    if len(sys.argv) < 2:
        print('사용법: python experiments/vision_probe/dump_content.py "<URL>" [dept_id]')
        sys.exit(1)
    url = sys.argv[1]
    dept_id = sys.argv[2] if len(sys.argv) > 2 else None
    store = Store(config.DB_PATH)
    dept = store.get_dept(dept_id) if dept_id else None
    if not dept:
        host = urlparse(url).netloc
        for d in store.active_depts():
            if urlparse(d.get("list_url") or "").netloc == host:
                dept = d
                break
    store.close()
    if not dept:
        print(f"학과 못 찾음(host={urlparse(url).netloc}) → dept_id 인자로 지정")
        sys.exit(1)
    sel = dept.get("content_selector")
    print(f"[dept] {dept['dept_id']}  content_selector = {sel}\n")
    raw = Fetcher()._content_generic(url, sel)   # 정제 전 원본 선택 요소
    if not raw:
        print("선택 요소 없음 → content_selector가 이 페이지와 안 맞음")
        sys.exit(1)
    low = raw.lower()
    print(f"[신호] <time> 태그: {'있음' if '<time' in low else '없음'} · "
          f"class에 date/일자: {'있음' if ('date' in low or '일자' in low or 'reg' in low) else '없음'}\n")
    print("──── content_selector 요소 HTML (앞 4000자) ────")
    print(raw[:4000])


if __name__ == "__main__":
    main()
