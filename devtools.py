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

from pipeline import _process_new_item
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
        picked.append((dept, label, top))
        c.log(f"[debug 대상] {label} :: {top['title'][:50]}")

    if not picked:
        c.log("[debug] 재요약 대상을 찾지 못함(크롤 실패/빈 목록).")
        return

    c.log(f"[debug] {len(picked)}개 학과만 처리 → 요약 시작 (전체 크롤 안 함)")
    for dept, label, top in picked:
        try:
            await _process_new_item(c, dept, {"title": top["title"], "url": top["url"]})
            await asyncio.to_thread(c.store.mark_seen, dept["dept_id"], [top["url"]])
        except Exception as e:
            c.log(f"[debug 처리 실패] {label}: {e}")
    await drain(c)

    # 결과 출력
    print("\n=== 재요약 결과 ===")
    for dept, label, top in picked:
        row = await asyncio.to_thread(_find_notice_by_url, c.store, top["url"])
        if not row:
            print(f"  [미처리] {label} :: {top['title'][:40]}")
            continue
        status = row["status"]
        engine = row["summary_engine"] or "-"
        summ = row["summary"] or ""
        print(f"\n  ● {label} [{status}/{engine}] {top['title'][:50]}")
        if summ:
            for ln in summ.splitlines():        # 불릿 줄바꿈 보존
                print(f"    {ln}")
        else:
            print("    (요약 없음)")


def _find_notice_by_url(store, url):
    with store._lock:
        r = store._con.execute("SELECT * FROM notices WHERE url=? ORDER BY id DESC LIMIT 1", (url,)).fetchone()
    return dict(r) if r else None


async def redo_search(c, query: str):
    """제목에 query가 든 공지들을 검색·나열 → 하나 선택 → 저장된 콘텐츠로 재요약·재발송.
    크롤 없이 이미 수집된 notices를 대상으로 함(프롬프트/비전 테스트용). 예: main.py redo "수강신청"."""
    rows = await asyncio.to_thread(c.store.search_notices, query)
    if not rows:
        c.log(f"[redo] '{query}' 검색 결과 없음 (notices 테이블에 수집된 것만 검색됨)")
        return
    print()
    for i, r in enumerate(rows, 1):
        dept = await asyncio.to_thread(c.store.get_dept, r["dept_id"]) or {}
        name = dept.get("name_ko") or r["dept_id"]
        print(f"[{i}] {name}({r['dept_id']}) | \"{r['title']}\"")
    try:
        sel = input(f"\n공지가 {len(rows)}개 검색되었습니다. 디버깅할 공지를 선택하세요. (1-{len(rows)}) : ").strip()
        pick = rows[int(sel) - 1]
    except (ValueError, IndexError, EOFError):
        c.log("[redo] 선택 취소/오류")
        return
    await _reprocess_notice(c, pick["id"])


async def _reprocess_notice(c, notice_id: int):
    """저장된 공지를 재처리: 상태 초기화 → D1 재발송(현재 라우팅=mono 등) → 재요약(edit)."""
    notice = await asyncio.to_thread(c.store.get_notice, notice_id)
    dept = await asyncio.to_thread(c.store.get_dept, notice["dept_id"]) or {}
    await asyncio.to_thread(c.store.set_status, notice_id, "detected")
    try:
        ch, mid = await asyncio.to_thread(c.notifier.send_new, notice, dept)
        await asyncio.to_thread(c.store.set_notified, notice_id, ch, mid)
    except Exception as e:
        c.log(f"[redo 발송 실패] {e}")
    await c.queue.put(notice_id)
    await drain(c)
    row = await asyncio.to_thread(c.store.get_notice, notice_id)
    print(f"\n● [{row['status']}/{row['summary_engine'] or '-'}] {notice['title'][:50]}")
    print(row["summary"] or f"(요약 없음 · fail_reason={row.get('fail_reason')})")
