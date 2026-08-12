# -*- coding: utf-8 -*-
"""
devtools.py — 개발/디버깅 도구.

debug_resummarize(c, n)  : 임의 N개 학과의 목록 맨 위(최신) 공지를 강제 재처리(크롤 X). `redo N`.
query_notices(c, query)  : 제목 검색 → 선택 → [재처리 | DB에서 제거]. `query "검색어"`.

재처리 깊이·발송처는 `--dst` / `--nosummary` 를 그대로 따른다(_process_new_item 재사용).
"""
import asyncio
import random

from pipeline import _process_new_item
from summarize.worker import drain


def _find_notice_by_url(store, url):
    with store._lock:
        r = store._con.execute("SELECT * FROM notices WHERE url=? ORDER BY id DESC LIMIT 1", (url,)).fetchone()
    return dict(r) if r else None


async def _reprocess(c, dept, title, url):
    """전송(D1)은 즉시, 요약은 큐에 '적재만' 한다(드레인은 호출자가 일괄). 반환 nid.
    → 여러 건을 돌릴 때 앞 건의 요약을 기다리지 않고 다음 건을 바로 전송(run과 동일 semantics)."""
    await asyncio.to_thread(c.store.forget_url, dept["dept_id"], url)   # 새로 promote되게 비움
    return await _process_new_item(c, dept, {"title": title, "url": url})


def _report_one(c, dept, title, url):
    """드레인 이후 요약 상태 출력."""
    row = _find_notice_by_url(c.store, url)
    label = f"{dept.get('name_ko') or ''}({dept['dept_id']})"
    if not row:
        print(f"\n  [미처리] {label} :: {title[:40]}")
        return
    print(f"\n● {label} [{row['status']}/{row['summary_engine'] or '-'}] {title[:50]}")
    if row.get("summary"):
        for ln in row["summary"].splitlines():
            print(f"    {ln}")
    else:
        print(f"    (요약 없음 · fail_reason={row.get('fail_reason')})")


async def debug_resummarize(c, n: int = 10):
    """임의 N개 학과의 최신 공지 1건씩 강제 재처리(크롤로 목록만 훑음). 프롬프트/품질 튜닝용."""
    depts = [d for d in await asyncio.to_thread(c.store.active_depts)
             if c.store.is_seeded(d["dept_id"])]
    random.shuffle(depts)

    picked = []
    for dept in depts:
        if len(picked) >= n:
            break
        try:
            items = await asyncio.to_thread(c.fetcher.scrape_list, dept, 1)
        except Exception as e:
            c.log(f"[redo skip] {dept['dept_id']}: {e}")
            continue
        if not items:
            continue
        top = items[0]
        picked.append((dept, top))
        c.log(f"[redo 대상] {dept.get('name_ko') or ''}({dept['dept_id']}) :: {top['title'][:50]}")

    if not picked:
        c.log("[redo] 재처리 대상을 찾지 못함(크롤 실패/빈 목록/ N=0).")
        return
    c.log(f"[redo] {len(picked)}개: 먼저 전부 전송(D1) → 요약은 이어서 일괄 처리(전송이 요약을 안 기다림)")
    # 1) 전송 먼저 — 앞 건의 요약 완료를 기다리지 않고 다음 건으로
    for dept, top in picked:
        try:
            await _reprocess(c, dept, top["title"], top["url"])
        except Exception as e:
            c.log(f"[redo 처리 실패] {dept['dept_id']}: {e}")
    # 2) 쌓인 요약을 일괄 드레인
    await drain(c)
    # 3) 결과 출력(요약 상태 확인)
    print("\n=== 재처리 결과 ===")
    for dept, top in picked:
        _report_one(c, dept, top["title"], top["url"])


async def query_notices(c, query: str):
    """제목에 query가 든 공지 검색 → 번호 선택 → [1]재처리 / [2]DB에서 제거.
    이미 수집된 notices(seeded 포함) 대상. 예: main.py query "수강신청"."""
    rows = await asyncio.to_thread(c.store.search_notices, query)
    if not rows:
        c.log(f"[query] '{query}' 검색 결과 없음")
        return
    print()
    for i, r in enumerate(rows, 1):
        dept = await asyncio.to_thread(c.store.get_dept, r["dept_id"]) or {}
        name = dept.get("name_ko") or r["dept_id"]
        print(f"[{i}] {name}({r['dept_id']}) [{r['status']}] | \"{r['title']}\"")
    try:
        sel = input(f"\n공지가 {len(rows)}개 검색되었습니다. 처리할 공지를 선택하세요. (1-{len(rows)}) : ").strip()
        pick = rows[int(sel) - 1]
    except (ValueError, IndexError, EOFError):
        c.log("[query] 선택 취소/오류")
        return
    try:
        act = input("동작 선택 — [1] 재처리  [2] DB에서 제거 : ").strip()
    except EOFError:
        return
    if act == "2":
        await asyncio.to_thread(c.store.delete_notice, pick["id"])
        c.log(f"[query] DB에서 제거됨: \"{pick['title'][:40]}\" (다음 크롤에 재감지됨)")
        return
    dept = await asyncio.to_thread(c.store.get_dept, pick["dept_id"]) or {"dept_id": pick["dept_id"]}
    await _reprocess(c, dept, pick["title"], pick["url"])   # 전송 + 요약 큐 적재
    await drain(c)                                          # 단건 → 바로 드레인해 결과 표시
    _report_one(c, dept, pick["title"], pick["url"])
