#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init/resummarize_failed.py — 요약 실패(summary_failed) 공지를 다시 요약하고 디스코드 메시지를 수정(일회성).

동작: 대상 공지를 한 건씩 summarize_one 에 넣는다(실서비스 워커와 같은 경로).
  · 이미 나간 디스코드 메시지가 있으면 그 메시지의 임베드를 PATCH로 수정한다(새 메시지 발송 X).
  · 요약 결과는 DB(summary·summary_engine·status)에 저장 → MCP 서버는 DB를 매 요청 읽으므로 즉시 반영.
  · 로컬 LLM 실패 시 클라우드(Google AI Studio)로 폴백(summarize/llm.py FallbackSummarizer).
  · 또 실패하면 기존처럼 summary_failed 로 남고, 감시채널에 '요약 실패' 디버그가 1건 간다.

실행(★ notice.db 있는 venv, 저장소 루트에서):
  python init/resummarize_failed.py --list          # 대상만 출력(LLM·DB·디스코드 변경 없음)
  python init/resummarize_failed.py --limit 5       # 5건만 시험
  python init/resummarize_failed.py --ids 1706,2103 # 지정 공지만
  python init/resummarize_failed.py                 # 전체
옵션: --text-only / --image-only (이미지 첨부 유무로 대상 한정)

주의: 실서비스(sauron:crawler)와 동시에 돌리면 같은 로컬 LLM(동시 1슬롯)을 두고 경쟁해 둘 다 느려진다.
  중간에 Ctrl-C 하면 처리 중이던 1건은 'summarizing'으로 남는다 → 서비스 재시작 시 자동 재처리되거나,
  이 스크립트는 summarizing 상태도 대상으로 잡으므로 다시 돌리면 이어진다(--include-stuck).
"""
import asyncio
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import config  # noqa: E402


def _opt(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _targets(store):
    statuses = ["summary_failed"] + (["summarizing"] if "--include-stuck" in sys.argv else [])
    q = (f"SELECT id, dept_id, title, images_json, discord_message_id FROM notices "
         f"WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id")
    with store._lock:
        rows = [dict(r) for r in store._con.execute(q, statuses).fetchall()]
    ids = _opt("--ids")
    if ids:
        want = {int(x) for x in ids.split(",") if x.strip()}
        rows = [r for r in rows if r["id"] in want]
    has_img = lambda r: bool(json.loads(r["images_json"] or "[]"))   # noqa: E731
    if "--text-only" in sys.argv:
        rows = [r for r in rows if not has_img(r)]
    if "--image-only" in sys.argv:
        rows = [r for r in rows if has_img(r)]
    limit = _opt("--limit")
    if limit:
        rows = rows[:int(limit)]
    return rows, has_img


async def _run(c, rows):
    from summarize.worker import summarize_one
    ok = fail = 0
    t_all = time.time()
    for i, r in enumerate(rows, 1):
        t0 = time.time()
        try:
            await summarize_one(c, r["id"])
        except Exception as e:              # summarize_one 자체가 터진 예외(버그) — 다음 건 계속
            c.log(f"[재요약 예외] id={r['id']}: {e}")
        n = c.store.get_notice(r["id"]) or {}
        good = n.get("status") == "done"
        ok += good
        fail += not good
        el = time.time() - t_all
        eta = el / i * (len(rows) - i)
        c.log(f"[재요약 {i}/{len(rows)}] id={r['id']} {'성공' if good else '실패'} "
              f"({n.get('summary_engine') or '-'}) {time.time() - t0:.1f}s · 남은 예상 {eta / 60:.0f}분")
    c.log(f"[재요약 끝] 성공 {ok} · 실패 {fail} · 총 {(time.time() - t_all) / 60:.1f}분")


def main():
    from main import build_components
    # dst=poly: 메시지가 이미 있는 공지는 dst와 무관하게 '그 메시지'를 수정한다.
    #   (메시지 ID가 없는 공지가 섞였을 때만 dst에 따라 새로 발송 — 실서비스와 동일하게 학과채널)
    c = build_components(dst=_opt("--dst", "poly"))
    try:
        rows, has_img = _targets(c.store)
        n_img = sum(1 for r in rows if has_img(r))
        n_nomsg = sum(1 for r in rows if not r["discord_message_id"])
        c.log(f"[재요약 대상] {len(rows)}건 (이미지 {n_img} · 본문만 {len(rows) - n_img} · "
              f"디스코드 메시지 없음 {n_nomsg}) · 로컬 {config.LLM_BASE_URL} · "
              f"클라우드 {'ON ' + config.LLM_CLOUD_MODEL if getattr(c.summarizer, 'cloud', None) else 'OFF'}")
        if "--list" in sys.argv:
            for r in rows:
                print(f"  id={r['id']:>5} {r['dept_id']:<16} {'🖼' if has_img(r) else '  '} {r['title'][:50]}")
            return
        if not rows:
            return
        if c.notifier.dry:
            c.log("[경고] 봇 토큰 없음 → 디스코드 수정은 시뮬레이션만 됨(DB는 갱신)")
        asyncio.run(_run(c, rows))
    except KeyboardInterrupt:
        c.log("[재요약 중단] Ctrl-C — 처리 중이던 1건은 'summarizing'으로 남음(--include-stuck 로 재개)")
    finally:
        from summarize.llm import request_shutdown
        request_shutdown()
        c.store.close()


if __name__ == "__main__":
    from core import vkpath
    vkpath.ensure()        # OCR Vulkan 로더 경로
    main()
