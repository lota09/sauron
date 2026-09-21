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
from core import errors
from core.crawl_health import CrawlHealth
from crawl.diff import detect_new, FetchEmpty, TooManyNew


def _label(dept):
    """로그용 라벨: '전자공학부(ee)' 형태. name_ko 없으면 dept_id."""
    n = dept.get("name_ko")
    return f"{n}({dept['dept_id']})" if n else dept["dept_id"]


class Components:
    def __init__(self, store, fetcher, summarizer, notifier, queue, logger=None, nosummary=False):
        self.store = store
        self.fetcher = fetcher
        self.summarizer = summarizer
        self.notifier = notifier
        self.queue = queue
        self.logger = logger
        self.nosummary = bool(nosummary)   # 요약(+상세fetch) 생략
        self._health = None

    @property
    def health(self):
        """학과별 수집 실패 상태(상태 변화만 알림). 첫 사용 때 DB에서 실패 중인 학과를 읽어온다."""
        if self._health is None:
            self._health = CrawlHealth(self.store, self.notifier, self.log)
        return self._health

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
        did, label = dept["dept_id"], _label(dept)
        if err is not None and not isinstance(err, TooManyNew):
            # 실패(빈 목록 포함)는 '상태가 바뀔 때만' 감시채널로. 반복은 로그 한 줄(core/crawl_health.py).
            #   빈 목록도 실패로 친다: 셀렉터가 어긋나면 에러 없이 0건이 되어 조용히 수집이 끊긴다
            #   (평생교육학과가 사이트 개편 후 3주간 이 상태였다).
            hint = "목록이 비어 있음 — 셀렉터가 안 맞거나 사이트가 개편됐을 수 있음" if isinstance(err, FetchEmpty) else ""
            await asyncio.to_thread(c.health.failed, did, label, err, hint)
            continue
        await asyncio.to_thread(c.health.ok, did, label)   # 목록을 받아왔으면 수집은 정상
        if isinstance(err, TooManyNew):
            # 목록은 받아왔지만 신규가 비정상적으로 많음 = 1회성 사건(전량을 '본 것'으로 전진시켰으므로 반복 안 됨)
            c.log(f"[대량알림 차단] {label}: {err}")
            await asyncio.to_thread(c.notifier.debug, f"**대량알림 차단** · {label} · 신규 {err.count}건"
                                                      f" > UPDATE_LIMIT({config.UPDATE_LIMIT}) — 사이트 구조 변경 의심")
            continue

        total_new += len(new_items)
        for item in new_items:
            try:
                await _process_new_item(c, dept, item)
            except Exception as e:
                c.log(f"[신규처리 실패] {label} {item.get('title','')[:30]}\n{errors.full(e)}")
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
