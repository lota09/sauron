# -*- coding: utf-8 -*-
"""
pipeline.py — 오케스트레이션.

Components: 주입 가능한 구성요소 묶음(테스트에서 fake 교체).
crawl_pass(): 전 학과 크롤 → 차집합 → (신규) 콘텐츠 fetch → DB insert → 디스코드 발송(D1) → 큐 적재.
run_once():   부팅 재적재 + 1회 크롤 + 큐 드레인 (테스트/수동실행).
"""
import asyncio
import json

import config
from crawl.diff import detect_new, FetchEmpty, TooManyNew
from crawl.fetcher import Fetcher, FetchError


def _label(dept):
    """로그용 라벨: '전자공학부(ee)' 형태. name_ko 없으면 dept_id."""
    n = dept.get("name_ko")
    return f"{n}({dept['dept_id']})" if n else dept["dept_id"]


class Components:
    def __init__(self, store, fetcher, ocr, summarizer, notifier, queue, clova=None, logger=None):
        self.store = store
        self.fetcher = fetcher
        self.ocr = ocr
        self.summarizer = summarizer
        self.notifier = notifier
        self.queue = queue
        self.clova = clova
        self.logger = logger

    def log(self, msg):
        (self.logger.info if self.logger else print)(msg)


async def _process_new_item(c, dept, item):
    """신규 1건: 콘텐츠 fetch → insert → 발송 → 큐 적재."""
    detail = await asyncio.to_thread(c.fetcher.fetch_content, dept, item["url"])
    images_json = json.dumps(detail.get("images") or [], ensure_ascii=False)
    notice_id = await asyncio.to_thread(
        c.store.insert_notice, dept["dept_id"], item["title"], item["url"],
        detail.get("content"), images_json)
    if notice_id is None:
        return None  # url 중복(이미 있음)
    notice = await asyncio.to_thread(c.store.get_notice, notice_id)
    # D1: 감지 즉시 발송(제목+링크). 실패해도 요약큐는 태움.
    try:
        channel_id, message_id = await asyncio.to_thread(c.notifier.send_new, notice, dept)
        await asyncio.to_thread(c.store.set_notified, notice_id, channel_id, message_id)
    except Exception as e:
        c.log(f"[발송 실패] {item['title'][:30]}: {e}")
    await c.queue.put(notice_id)
    return notice_id


async def crawl_pass(c):
    """1회 크롤 패스. 학과 단위 오류는 격리(감시채널 디버그)."""
    depts = await asyncio.to_thread(c.store.active_depts)
    total_new = 0
    for dept in depts:
        try:
            new_items = await asyncio.to_thread(detect_new, c.store, c.fetcher, dept)
        except FetchEmpty as e:
            c.log(f"[빈 목록] {_label(dept)}: {e}")
            continue
        except TooManyNew as e:
            c.log(f"[대량알림 차단] {_label(dept)}: {e}")
            c.notifier.debug(f"{_label(dept)} 신규 {e.count}건 초과 — 사이트 구조 변경 의심")
            continue
        except (FetchError, Exception) as e:
            c.log(f"[크롤 실패] {_label(dept)}: {e}")
            c.notifier.debug(f"크롤 실패: {_label(dept)}\n{e}")
            continue

        for item in new_items:
            try:
                nid = await _process_new_item(c, dept, item)
                if nid:
                    total_new += 1
            except Exception as e:
                c.log(f"[신규처리 실패] {_label(dept)} {item.get('title','')[:30]}: {e}")
    c.log(f"[crawl_pass] 신규 {total_new}건 감지·적재")
    return total_new


async def run_once(c):
    """부팅 재적재 + 1회 크롤 + 큐 드레인."""
    from summarize.worker import drain
    n = c.queue.requeue_pending(c.store)
    if n:
        c.log(f"[부팅 재적재] 미완 요약 {n}건")
    await crawl_pass(c)
    await drain(c)
