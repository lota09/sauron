# -*- coding: utf-8 -*-
"""summarize/worker.py — 요약 워커 (OCR→LLM→DB→디스코드 edit). asyncio + to_thread."""
import asyncio
import json

import config
from summarize.llm import SummaryError


async def summarize_one(c, notice_id: int):
    """단일 공지 요약 처리. c=Components."""
    notice = await asyncio.to_thread(c.store.get_notice, notice_id)
    if not notice or notice.get("status") == "done":
        return
    dept = await asyncio.to_thread(c.store.get_dept, notice["dept_id"]) or {}
    await asyncio.to_thread(c.store.set_status, notice_id, "summarizing")

    # OCR (이미지 있을 때만)
    ocr_text = ""
    try:
        images = json.loads(notice.get("images_json") or "[]")
    except Exception:
        images = []
    if images:
        parts = []
        for img in images:
            t = await asyncio.to_thread(c.ocr.extract, img.get("url", ""))
            if t:
                parts.append(t)
        ocr_text = "\n".join(parts)

    # LLM 요약 (동시성 제한)
    summary = engine = None
    err = None
    try:
        async with c.queue.sem:
            summary, engine = await asyncio.to_thread(
                c.summarizer.summarize, notice["title"], notice.get("content_raw") or "", ocr_text or None)
    except SummaryError as e:
        err = e
    except Exception as e:
        err = e

    # 실패 시 Clova 폴백(옵션)
    if summary is None and c.clova and config.CLOVA_ENABLE:
        try:
            async with c.queue.sem:
                summary, engine = await asyncio.to_thread(
                    c.clova.summarize, notice["title"], notice.get("content_raw") or "", ocr_text or None)
            c.notifier.debug(f"Clova 폴백 사용: {notice['title'][:40]}")
        except Exception as e:
            err = e

    if summary:
        await asyncio.to_thread(c.store.set_summary, notice_id, summary, engine, ocr_text or None, "done")
        updated = await asyncio.to_thread(c.store.get_notice, notice_id)
        try:
            await asyncio.to_thread(c.notifier.edit_summary,
                                    notice.get("discord_channel_id"), notice.get("discord_message_id"),
                                    updated, dept)
        except Exception as e:
            c.log(f"[edit 실패] {notice['title'][:30]}: {e}")
        c.log(f"[요약 완료] {engine} :: {notice['title'][:40]}")
    else:
        # 요약 실패 = 요약만 포기(알림은 이미 나감). 누락 0.
        await asyncio.to_thread(c.store.set_summary, notice_id, None, None, ocr_text or None, "summary_failed")
        c.log(f"[요약 실패] {notice['title'][:40]} ({err})")
        c.notifier.debug(f"요약 실패: {notice['title'][:40]}\n{err}")


async def worker_loop(c):
    """큐에서 인터럽트식으로 깨어나 처리."""
    while True:
        notice_id = await c.queue.get()
        try:
            await summarize_one(c, notice_id)
        except Exception as e:
            c.log(f"[worker 예외] notice={notice_id}: {e}")
        finally:
            c.queue.task_done()


async def drain(c):
    """run_once용: 큐가 빌 때까지 순차 처리."""
    while not c.queue.empty():
        notice_id = await c.queue.get()
        try:
            await summarize_one(c, notice_id)
        except Exception as e:
            c.log(f"[drain 예외] notice={notice_id}: {e}")
        finally:
            c.queue.task_done()
