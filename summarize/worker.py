# -*- coding: utf-8 -*-
"""summarize/worker.py — 요약 워커 (OCR→LLM→DB→디스코드 edit). asyncio + to_thread."""
import asyncio
import json

import config
from summarize.llm import SummaryError, EmptyContentError
from summarize.vision import to_data_url, HAVE_PIL


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

    # 이미지가 있으면 요약 요청에 무조건 첨부(텍스트 유무 무관). 최대 N장(컨텍스트/지연 상한).
    #   추출(images) → 인코딩 성공(data_urls). 둘의 차이 = 로드/포맷 실패로 '입력 못 한' 장수.
    data_urls = []
    if images and config.LLM_VISION:
        cand = images[:config.LLM_VISION_MAX_IMAGES]
        for img in cand:
            du = await asyncio.to_thread(to_data_url, img.get("url", ""), config.LLM_VISION_MAX_PX)
            if du:
                data_urls.append(du)
            else:
                c.log(f"[이미지 제외] 로드 실패 또는 너무 작음(아이콘) → LLM 입력 안 함: {img.get('url', '')[:80]}")
        if len(images) > config.LLM_VISION_MAX_IMAGES:
            c.log(f"[이미지 상한] {len(images)}장 중 {config.LLM_VISION_MAX_IMAGES}장만 입력"
                  f"(LLM_VISION_MAX_IMAGES)")
        if data_urls and not HAVE_PIL:
            c.log("[비전] Pillow 미설치 → 다운스케일 없이 원본 전송(2479px 다중이미지는 요약이 뭉개질 위험). "
                  "`pip install pillow` 권장")
        elif data_urls and config.LLM_VISION_MAX_PX < 768:
            c.log(f"[비전] LLM_VISION_MAX_PX={config.LLM_VISION_MAX_PX} < 768 → 글자 뭉개짐·환각 위험(실측). "
                  "768~1024 권장")

    # LLM 요약 (동시성 제한). 재시도는 summarize() 내부. 본문·OCR·이미지 모두 없으면 no_content.
    summary = engine = None
    err = None
    no_content = False
    try:
        async with c.queue.sem:
            summary, engine = await asyncio.to_thread(
                c.summarizer.summarize, notice["title"], notice.get("content_raw") or "",
                ocr_text or None, data_urls or None)
    except EmptyContentError as e:
        no_content = True
        err = e
    except SummaryError as e:
        err = e
    except Exception as e:
        err = e

    # 실패(내용없음 제외) 시 Clova 폴백(옵션). 현재 비활성(자리만).
    if summary is None and not no_content and c.clova and config.CLOVA_ENABLE:
        try:
            async with c.queue.sem:
                summary, engine = await asyncio.to_thread(
                    c.clova.summarize, notice["title"], notice.get("content_raw") or "", ocr_text or None)
            c.notifier.debug(f"Clova 폴백 사용: {notice['title'][:40]}")
        except Exception as e:
            err = e

    async def _edit(status_notice):
        try:
            await asyncio.to_thread(c.notifier.edit_summary,
                                    notice.get("discord_channel_id"), notice.get("discord_message_id"),
                                    status_notice, dept)
        except Exception as e:
            c.log(f"[edit 실패] {notice['title'][:30]}: {e}")

    if summary:
        await asyncio.to_thread(c.store.set_summary, notice_id, summary, engine, ocr_text or None, "done")
        await _edit(await asyncio.to_thread(c.store.get_notice, notice_id))
        # 이미지 입력 현황을 로그로 노출: 실제 LLM에 넣은 장수 / 추출 장수.
        #   (LLM이 그 이미지를 '이해'했는지는 여기서 알 수 없다 — 입력 여부만 확인 가능.)
        # 전송 페이로드 KB도 노출(서버가 큰 이미지를 조용히 버릴 때 진단용).
        kb = sum(len(u) for u in data_urls) * 3 // 4 // 1024 if data_urls else 0
        img_note = f" (이미지 {len(data_urls)}/{len(images)}장·{kb}KB 입력)" if images else ""
        c.log(f"[요약 완료] {engine} :: {notice['title'][:40]}{img_note}")
    elif no_content:
        # 제목만 있고 본문·OCR 모두 없음 → LLM에 안 보냄. '요약할 내용이 없습니다' 표기(재시도 X).
        # 실패가 아니므로 디버그 발송 안 함. 사유는 DB(fail_reason)에만 기록(사후 분석용).
        await asyncio.to_thread(c.store.set_summary, notice_id, None, None, ocr_text or None,
                                "no_content", "본문·OCR·이미지 없음 또는 이미지 로드 실패")
        await _edit(await asyncio.to_thread(c.store.get_notice, notice_id))
        c.log(f"[내용 없음] {notice['title'][:40]}")
    else:
        # 요약 실패 = 요약만 포기(알림은 이미 나감). 누락 0. 재시도 소진 → 영구 실패(재크롤/재부팅에도 재시도 X).
        await asyncio.to_thread(c.store.set_summary, notice_id, None, None, ocr_text or None,
                                "summary_failed", str(err)[:500])   # 사유 DB 기록
        await _edit(await asyncio.to_thread(c.store.get_notice, notice_id))  # SUMMARY_FAIL_NOTE 표시
        c.log(f"[요약 실패] {notice['title'][:40]} ({err})")
        # 모든 LLM 실패는 반드시 디버그 발송(사유 누적 메시지 포함)
        c.notifier.debug(f"요약 실패: {notice['title'][:40]}\n사유: {err}")


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
