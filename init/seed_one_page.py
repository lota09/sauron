#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init/seed_one_page.py — json_api 학과의 특정 페이지 목록을 DB에 수동 시딩(일회성).

main.py 무수정. 기존 함수만 재활용:
  · Fetcher._get_json(url, headers)   — API 호출
  · apiparse.dig(data, list_path)     — 목록 배열 추출
  · Store.seed_rows(dept_id, items)   — INSERT OR IGNORE 'seeded'(멱등)

용도: 1페이지만 시딩돼 있어 2페이지 이하 공지가 DB에 없을 때, 그 페이지를 넣어
      `main.py query "<제목단어>"` 로 선택·재처리할 수 있게 한다.

주의: media/startup API는 page_base=0(0-based)이라 웹의 'page=2'와 어긋날 수 있음.
      확실히 하려면 여러 페이지를 한 번에 시딩(중복은 자동 무시).

실행(★ 사이트·notice.db 접근되는 venv):
  python init/seed_one_page.py media 2          # API page=2 만
  python init/seed_one_page.py media 0 1 2 3    # 앞쪽 여러 페이지(권장, 목표 확실 포함)
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
from db.store import Store
from crawl.fetcher import Fetcher
from crawl import apiparse


def main():
    if len(sys.argv) < 3:
        print('사용법: python init/seed_one_page.py <dept_id> <page> [page ...]')
        sys.exit(1)
    dept_id = sys.argv[1]
    try:
        pages = [int(x) for x in sys.argv[2:]]
    except ValueError:
        print("page 는 정수여야 함"); sys.exit(1)

    store = Store(config.DB_PATH)
    dept = store.get_dept(dept_id)
    if not dept:
        print(f"dept 없음: {dept_id}"); store.close(); sys.exit(1)
    if dept.get("fetch_type") != "json_api":
        print(f"{dept_id}: json_api 전용(현재 {dept.get('fetch_type')}) — html은 목록 URL로 직접 시딩 필요")
        store.close(); sys.exit(1)

    cfg = json.loads(dept["fetch_config"])
    f = Fetcher()

    merged, seen = [], set()
    for p in pages:
        url = cfg["list_url"].format(page=p)
        try:
            data = f._get_json(url, cfg.get("headers"))          # 기존 함수
        except Exception as e:
            print(f"  page {p}: API 실패 — {e}"); continue
        arr = apiparse.dig(data, cfg["list_path"]) or []          # 기존 함수
        cnt = 0
        for it in arr:
            if not isinstance(it, dict):
                continue
            cid = it.get(cfg["id_key"])
            title = (it.get(cfg["title_key"]) or "").strip()
            if cid is None or not title:
                continue
            nurl = cfg["url_template"].format(id=cid)
            if nurl in seen:
                continue
            seen.add(nurl)
            merged.append({"title": title, "url": nurl})
            cnt += 1
        print(f"  page {p}: {cnt}건")

    if not merged:
        print("[seed] 시딩할 항목 없음(페이지/설정 확인)"); store.close(); return

    before = len(store.seen_urls(dept_id))
    store.seed_rows(dept_id, merged)                              # 기존 함수(멱등)
    added = len(store.seen_urls(dept_id)) - before
    store.close()
    print(f"[seed] {dept_id}: 목록 {len(merged)}건 · 신규 시딩 {added}건 (기존 {len(merged) - added}건)")
    for it in merged:
        print(f"   - {it['title'][:64]}")
    print('\n다음: python main.py query "<제목에 든 단어>" --dst mono   → 번호 선택 → [1] 재처리')


if __name__ == "__main__":
    main()
