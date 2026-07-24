# -*- coding: utf-8 -*-
"""
devtools.py — 개발/디버깅 도구.

debug_resummarize(c, n):
  임의의 N개 학과에서 '가장 최신 공지 1개'를 seen에서 제거 → 크롤 1회 → 그 공지들이
  신규로 재감지되어 실제 콘텐츠 fetch + LLM 요약까지 수행됨.
  최초 실행이 seed(제목/URL만)라 요약 확인이 안 되는 문제를 해결.
  (시딩 상태·seen은 처리 후 자동 복원됨 — detect_new가 다시 seen에 기록)

  주의: '가장 최신'은 목록 page1의 맨 위 항목을 뜻하며, 이를 위해 각 학과 page1을
  실제로 크롤한다(사이트/셀렉터 동작도 겸사겸사 검증).
"""
import asyncio
import random

from pipeline import crawl_pass
from summarize.worker import drain


async def debug_resummarize(c, n: int = 10):
    depts = [d for d in await asyncio.to_thread(c.store.active_depts)
             if c.store.is_seeded(d["dept_id"])]
    random.shuffle(depts)

    picked = []          # (dept_id, url, title)
    for dept in depts:
        if len(picked) >= n:
            break
        try:
            items = await asyncio.to_thread(c.fetcher.scrape_list, dept, 1)
        except Exception as e:
            c.log(f"[debug skip] {dept['dept_id']}: {e}")
            continue
        if not items:
            continue
        top = items[0]   # 목록 맨 위 = 최신
        await asyncio.to_thread(c.store.forget_url, dept["dept_id"], top["url"])
        label = f"{dept.get('name_ko') or ''}({dept['dept_id']})"
        picked.append((label, top["url"], top["title"]))
        c.log(f"[debug 대상] {label} :: {top['title'][:50]}")

    if not picked:
        c.log("[debug] 재요약 대상을 찾지 못함(크롤 실패/빈 목록).")
        return

    c.log(f"[debug] {len(picked)}개 학과 최신공지 재감지 → 요약 시작")
    await crawl_pass(c)
    await drain(c)

    # 결과 출력
    print("\n=== 재요약 결과 ===")
    for label, url, title in picked:
        row = await asyncio.to_thread(_find_notice_by_url, c.store, url)
        if not row:
            print(f"  [미처리] {label} :: {title[:40]}")
            continue
        status = row["status"]
        engine = row["summary_engine"] or "-"
        summ = row["summary"] or ""
        print(f"\n  ● {label} [{status}/{engine}] {title[:50]}")
        if summ:
            for ln in summ.splitlines():        # 불릿 줄바꿈 보존
                print(f"    {ln}")
        else:
            print("    (요약 없음)")


def _find_notice_by_url(store, url):
    with store._lock:
        r = store._con.execute("SELECT * FROM notices WHERE url=? ORDER BY id DESC LIMIT 1", (url,)).fetchone()
    return dict(r) if r else None
