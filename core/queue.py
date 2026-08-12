# -*- coding: utf-8 -*-
"""core/queue.py — 요약 작업 큐 (인프로세스 인터럽트) + LLM 동시성 제한."""
import asyncio

import config


class WorkQueue:
    def __init__(self, max_concurrency: int = None):
        self.q: asyncio.Queue = asyncio.Queue()
        self.sem = asyncio.Semaphore(max_concurrency or config.LLM_MAX_CONCURRENCY)

    def put_nowait(self, notice_id: int):
        self.q.put_nowait(notice_id)

    async def put(self, notice_id: int):
        await self.q.put(notice_id)

    async def get(self) -> int:
        return await self.q.get()

    def task_done(self):
        self.q.task_done()

    def empty(self) -> bool:
        return self.q.empty()

    async def join(self):
        await self.q.join()

    def requeue_pending(self, store):
        """부팅 시 미완 요약(detected/notified/summarizing) 재적재 → 크래시 복원."""
        ids = store.pending_summary_ids()
        for nid in ids:
            self.q.put_nowait(nid)
        return len(ids)
