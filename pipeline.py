# -*- coding: utf-8 -*-
"""
pipeline.py — 오케스트레이션.

Components: 주입 가능한 구성요소 묶음(테스트에서 fake 교체). nosummary 플래그로 처리 깊이 조절.
crawl_pass(): 전 학과 크롤 → 차집합 → (신규) seeded 기록 → 처리(발송/요약).
run_once():   부팅 재적재 + 1회 크롤 + 큐 드레인. --nosummary면 요약·재적재 생략(=시딩류).
"""
import asyncio
import json

import config
from crawl.diff import detect_new, FetchEmpty, TooManyNew


def _label(dept):
    """로그용 라벨: '전자공학부(ee)' 형태. name_ko 없으면 dept_id."""
    n = dept.get("name_ko")
    return f"{n}({dept['dept_id']})" if n else dept["dept_id"]


class Components:
    def __init__(self, store, fetcher, ocr, summarizer, notifier, queue,
                 clova=None, logger=None, nosummary=False):
        self.store = store
        self.fetcher = fetcher
        self.ocr = ocr
        self.summarizer = summarizer
        self.notifier = notifier
        self.queue = queue
        self.clova = clova
        self.logger = logger
        self.nosummary = bool(nosummary)   # 요약(+상세fetch) 생략

    def log(self, msg):
        (self.logger.info if self.logger else print)(msg)


async def _process_new_item(c, dept, item):
    """신규 1건 처리. --nosummary / 발송여부(dst)에 따라 깊이 조절.
      · nosummary + 발송안함(dst null): 순수 시딩 → 'seeded' 유지, 처리 안 함.
      · nosummary + 발송함:            상세fetch 생략, D1(제목+링크)만 발송.
      · 요약함:                        상세fetch → (발송 시)D1 → 요약큐.
    """
    nosummary = c.nosummary
    send = c.notifier.send_enabled          # 보낼 의사(dst != null)
    if nosummary and not send:
        return None                         # 순수 시딩(이미 seed_rows로 'seeded' 기록됨)

    if nosummary:
        content = images_json = None        # 내용은 요약에만 필요 → 생략(자원 절약)
    else:
        detail = await asyncio.to_thread(c.fetcher.fetch_content, dept, item["url"])
        content = detail.get("content")
        images_json = json.dumps(detail.get("images") or [], ensure_ascii=False)

    nid = await asyncio.to_thread(
        c.store.promote_notice, dept["dept_id"], item["title"], item["url"], content, images_json)
    if nid is None:
        return None

    if send:                                # 발송 대상(dst != null)
        notice = await asyncio.to_thread(c.store.get_notice, nid)
        try:
            channel_id, message_id = await asyncio.to_thread(c.notifier.send_new, notice, dept)
            await asyncio.to_thread(c.store.set_notified, nid, channel_id, message_id)
        except Exception as e:
            c.log(f"[발송 실패] {item['title'][:30]}: {e}")
    if not nosummary:
        await c.queue.put(nid)              # 요약 워커로
    return nid


async def crawl_pass(c):
    """1회 크롤 패스. 학과 단위 오류는 격리(감시채널 디버그)."""
    depts = await asyncio.to_thread(c.store.active_depts)

    # 목록 fetch(detect_new)를 동시에 — 한 사이트가 막혀도 나머지는 진행(전체 지연 = 합계가 아니라 최장 1곳).
    #   신규 처리(_process_new_item: 상세fetch+전송)는 감지 결과를 모아 순차로(전송 파이프라인 구조 유지).
    sem = asyncio.Semaphore(config.CRAWL_CONCURRENCY)

    async def _detect(dept):
        async with sem:
            try:
                return dept, await asyncio.to_thread(detect_new, c.store, c.fetcher, dept), None
            except Exception as e:
                return dept, None, e

    results = await asyncio.gather(*[_detect(d) for d in depts])

    total_new = 0
    for dept, new_items, err in results:
        if err is not None:
            if isinstance(err, FetchEmpty):
                c.log(f"[빈 목록] {_label(dept)}: {err}")
            elif isinstance(err, TooManyNew):
                c.log(f"[대량알림 차단] {_label(dept)}: {err}")
                c.notifier.debug(f"{_label(dept)} 신규 {err.count}건 초과 — 사이트 구조 변경 의심")
            else:
                c.log(f"[크롤 실패] {_label(dept)}: {err}")
                c.notifier.debug(f"크롤 실패: {_label(dept)}\n{err}")
            continue

        total_new += len(new_items)
        for item in new_items:
            try:
                await _process_new_item(c, dept, item)
            except Exception as e:
                c.log(f"[신규처리 실패] {_label(dept)} {item.get('title','')[:30]}: {e}")
    c.log(f"[crawl_pass] 신규 {total_new}건 감지"
          + ("(시딩만)" if (c.nosummary and not c.notifier.send_enabled) else "·처리"))
    return total_new


async def run_once(c):
    """부팅 재적재 + 1회 크롤 + 큐 드레인. --nosummary면 요약·재적재 생략."""
    from summarize.worker import drain
    if not c.nosummary:
        n = c.queue.requeue_pending(c.store)
        if n:
            c.log(f"[부팅 재적재] 미완 요약 {n}건")
    await crawl_pass(c)
    if not c.nosummary:
        await drain(c)
    c.store.checkpoint()
