# -*- coding: utf-8 -*-
"""
devtools.py — 개발/디버깅 도구.

debug_resummarize(c, n)  : 임의 N개 학과의 목록 맨 위(최신) 공지를 강제 재처리(크롤 X). `redo N`.
                           depts/exclude로 학과 범위, per로 학과당 건수, random으로 1페이지에서 임의 선택.
query_notices(c, query)  : 제목 검색 → 선택 → [재처리 | DB에서 제거]. `query "검색어"`.
check_depts(c, depts)    : 학과별 수집 점검(목록 → 최신 공지 상세). 발송·DB 기록 없음. `check [--depts a,b]`.

재처리 깊이·발송처는 `--dst` / `--nosummary` 를 그대로 따른다(_process_new_item 재사용).
"""
import asyncio
import random as _random

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


async def debug_resummarize(c, n: int = 10, depts=None, exclude=(), per=1, random=False):
    """임의 N개 학과에서 공지를 강제 재처리(크롤로 목록만 훑음). 프롬프트/품질·신규 학과 동작 확인용.
      depts   : 이 dept_id들 중에서만 고름(없으면 전체). 모르는 id는 경고.
      exclude : 이 dept_id들은 뺌.
      per     : 학과당 몇 건(1페이지 안에서).
      random  : 1페이지에서 임의로 고름. False면 맨 위(최신)부터."""
    pool = [d for d in await asyncio.to_thread(c.store.active_depts)
            if c.store.is_seeded(d["dept_id"]) and d["dept_id"] not in set(exclude or ())]
    if depts:
        known = {d["dept_id"] for d in pool}
        for x in depts:
            if x not in known:
                c.log(f"[redo] '{x}' — 활성·시딩된 학과가 아니거나 제외됨 → 건너뜀")
        pool = [d for d in pool if d["dept_id"] in set(depts)]
    _random.shuffle(pool)

    picked, used = [], 0
    for dept in pool:
        if used >= n:
            break
        try:
            items = await asyncio.to_thread(c.fetcher.scrape_list, dept, 1)
        except Exception as e:
            c.log(f"[redo skip] {dept['dept_id']}: {e}")
            continue
        if not items:
            continue
        chosen = _random.sample(items, min(per, len(items))) if random else items[:per]
        used += 1
        for top in chosen:
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


STALE_DAYS = 30     # 이 기간 동안 신규를 한 번도 못 잡았으면 '조용히 끊겼을 수 있음'으로 표시


def _last_new(store):
    """학과별 마지막 '신규 감지'(시딩이 아니라 실제로 알림까지 간 공지) 시각."""
    with store._lock:
        rows = store._con.execute(
            "SELECT dept_id, MAX(created_at) FROM notices WHERE status <> 'seeded' GROUP BY dept_id").fetchall()
    return {r[0]: r[1] for r in rows}


async def check_depts(c, only=None):
    """학과마다 목록 1페이지 → 최신 공지 상세까지 실제로 받아 본다. 발송·DB 기록은 하지 않는다.
    보는 것: 목록 건수 · 본문 길이(0이면 content_selector 어긋남) · 이미지 · 걸린 시간 · 오류(어디서 왜)
            · 마지막 신규 감지일(오래됐으면 목록은 나오는데 새 글을 못 잡는 '조용한 고장'일 수 있음)."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta
    from core.errors import describe

    depts = await asyncio.to_thread(c.store.active_depts)
    if only:
        depts = [d for d in depts if d["dept_id"] in set(only)]
    last = await asyncio.to_thread(_last_new, c.store)
    failing = {k[len("crawl_fail:"):] for k in c.store.meta_with_prefix("crawl_fail:")}
    stale_before = (datetime.utcnow() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")

    def one(d):
        t0, r = time.time(), {"d": d, "n": 0, "first": "", "clen": None, "imgs": 0, "err": None}
        try:
            items = c.fetcher.scrape_list(d, 1)
            r["n"] = len(items)
            if items:
                r["first"] = items[0]["title"]
                det = c.fetcher.fetch_content(d, items[0]["url"])
                r["clen"], r["imgs"] = len(det["content"] or ""), len(det["images"])
        except Exception as e:
            r["err"] = describe(e).splitlines()[-2 if len(describe(e).splitlines()) > 1 else -1]
        r["sec"] = time.time() - t0
        return r

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, depts))

    bad = warn = 0
    print(f"\n{'':2} {'학과':<28} {'목록':>4} {'본문':>6} {'img':>3} {'초':>5}  마지막 신규   최신 공지 / 문제")
    for r in sorted(res, key=lambda r: (r["err"] is None, bool(r["clen"]), r["d"]["dept_id"])):
        d = r["d"]; did = d["dept_id"]
        ln = (last.get(did) or "")[:10]
        notes = []
        if r["err"]:
            mark = "❌"; notes.append(r["err"]); bad += 1
        elif r["n"] == 0:
            mark = "❌"; notes.append("목록 0건 — link_selector 어긋남?"); bad += 1
        elif not r["clen"]:
            mark = "⚠️"; notes.append("본문 0자 — content_selector 어긋남?"); warn += 1
        elif not ln or ln < stale_before:
            mark = "⚠️"; notes.append(f"{STALE_DAYS}일 넘게 신규 없음 — 조용한 고장인지 확인"); warn += 1
        else:
            mark = "✅"
        if did in failing:
            notes.append("(크롤러가 지금 실패 상태로 기록 중)")
        name = f"{d.get('name_ko') or did}({did})"
        clen = "-" if r["clen"] is None else r["clen"]
        print(f"{mark} {name[:28]:<28} {r['n']:>4} {clen:>6} {r['imgs']:>3} {r['sec']:>5.1f}  {ln or '없음':<11}  "
              + (" · ".join(notes) if notes else r["first"][:40]))
    print(f"\n점검 {len(res)}곳 · ✅ {len(res) - bad - warn} · ⚠️ {warn} · ❌ {bad}")
