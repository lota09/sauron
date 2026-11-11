# -*- coding: utf-8 -*-
"""summarize/worker.py — 요약 워커. asyncio + to_thread.

흐름(docs/local_llm_ocr_plan.md §2·§3.3):
  intake(c.queue, 공지 id) ─ router ─┬─ 이미지 없음 ───────────────────────────→ llm_q ─ LLM 워커 × LLM_MAX_CONCURRENCY
                                     └─ 이미지 있음 → ocr_q ─ OCR 워커 1개(GPU) ──→ llm_q
  · OCR 큐는 LLM 을 막지 않는다: 텍스트 공지는 OCR 을 기다리지 않고 바로 LLM 슬롯으로 간다.
  · OCR 워커는 이미지를 내려받고(to_data_url) 장마다 OCR 한 뒤, 텍스트를 붙여 llm_q 에 넣는다.
  · OCR 이 판독 실패(글자 OCR_MIN_CHARS 미만, 디자인 포스터 등)이거나 OCR 이 꺼졌으면 '로컬 생략' 표시 →
    LLM 단계에서 클라우드 비전(이미지 첨부)으로 바로 요약한다. 클라우드가 없으면 본문만으로 로컬 요약.
  · summarize_one(c, id) 은 같은 단계를 순차로 한 번에 도는 경로(drain·redo·재요약 스크립트용).

발송 시점은 config.SEND_AFTER_SUMMARY(pipeline.send_after_summary):
  · True(기본): 요약이 끝난 뒤 '제목+요약+링크'를 여기서 **처음** 보낸다(폰 푸시에 요약이 보인다).
  · False:      pipeline이 제목+링크를 먼저 보냈고, 여기서는 그 메시지를 수정한다(2단).
누락 0 보장(True일 때): 요약 결과를 DB에 쓸 때 상태는 아직 '미완(summarizing)'으로 두고,
  발송에 성공한 뒤에야 최종 상태(done/summary_failed/no_content)를 쓴다.
  → 발송 전에 죽거나 디스코드 전송이 실패하면 그 공지는 '미완'으로 남아 다음 부팅 때 재적재된다
    (core/queue.py requeue_pending). 요약이 이미 저장돼 있으면 LLM은 다시 부르지 않는다.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import config
from core import errors
from summarize.llm import SummaryError, EmptyContentError
from summarize.vision import to_data_url, HAVE_PIL
from pipeline import send_after_summary

log = logging.getLogger("sauron.ocr")


@dataclass
class Job:
    """한 공지의 요약 작업. router → (OCR) → LLM 단계로 넘어가며 채워진다."""
    notice: dict
    dept: dict
    images: list = field(default_factory=list)       # images_json (추출된 이미지 목록)
    data_urls: list = field(default_factory=list)    # 내려받기·인코딩 성공분(LLM_VISION_MAX_IMAGES 이내)
    ocr_texts: list = None                           # 장별 OCR 텍스트(OCR 안 했으면 None)
    skip_local: bool = False                         # OCR 판독 실패 → 로컬 생략, 클라우드 비전으로
    ocr_note: str = ""                               # 로그·폴백 사유용


# ── OCR 상태(프로세스 전역) ─────────────────────────────────────────────
class _OCRState:
    consec_fail = 0
    disabled_reason = None     # 문자열이면 OCR 꺼짐(재시작 전까지)


OCR_STATE = _OCRState()


def _ocr_enabled():
    return config.OCR_ENABLE and OCR_STATE.disabled_reason is None


def _disable_ocr(c, reason):
    if OCR_STATE.disabled_reason is None:
        OCR_STATE.disabled_reason = reason
        c.log(f"[OCR 비활성] {reason} → 이후 이미지 공지는 폴백 경로(클라우드 비전/본문만). 재시작하면 다시 켜짐")
        try:
            c.notifier.debug(f"**OCR 비활성**\n{reason}\n이후 이미지 공지는 클라우드 비전(또는 본문만)으로 요약합니다. "
                             f"크롤러 재시작 시 다시 켜집니다.")
        except Exception as de:
            c.log(f"[debug 전송 실패] {de}")


# ── 단계 0: 공지 로드 ──────────────────────────────────────────────────
async def _load_job(c, notice_id):
    """작업 생성. None = 처리할 것 없음(없거나 이미 끝남, 또는 '발송만' 처리 완료)."""
    notice = await asyncio.to_thread(c.store.get_notice, notice_id)
    if not notice or notice.get("status") == "done":
        return None
    dept = await asyncio.to_thread(c.store.get_dept, notice["dept_id"]) or {}

    # 요약은 이미 끝났고 발송만 못 한 건(발송 직전에 죽었거나 디스코드 전송이 실패) → LLM 생략, 발송만.
    if (notice.get("summary") or "").strip():
        c.log(f"[발송 재시도] id={notice_id} 요약은 이미 있음 → 발송만 :: {notice['title'][:40]}")
        await _publish(c, notice, dept, notice["summary"], notice.get("summary_engine"), "done", None)
        return None

    await asyncio.to_thread(c.store.set_status, notice_id, "summarizing")
    try:
        images = json.loads(notice.get("images_json") or "[]")
    except Exception:
        images = []
    return Job(notice=notice, dept=dept, images=images)


# ── 단계 1: 이미지 준비 + OCR (이미지 있는 공지만) ─────────────────────────
async def _prepare_images(c, job):
    """이미지 내려받기·축소 → data URL. 최대 LLM_VISION_MAX_IMAGES장."""
    dept = job.dept
    img_session = c.fetcher.session_for(dept) if dept and hasattr(c.fetcher, "session_for") else None
    cand = job.images[:config.LLM_VISION_MAX_IMAGES]
    for img in cand:
        du = await asyncio.to_thread(to_data_url, img.get("url", ""), config.LLM_VISION_MAX_PX,
                                     None, img_session)
        if du:
            job.data_urls.append(du)
        else:
            c.log(f"[이미지 제외] 로드 실패 또는 너무 작음(아이콘) → 입력 안 함: {img.get('url', '')[:80]}")
    if len(job.images) > config.LLM_VISION_MAX_IMAGES:
        c.log(f"[이미지 상한] {len(job.images)}장 중 {config.LLM_VISION_MAX_IMAGES}장만 입력"
              f"(LLM_VISION_MAX_IMAGES)")
    if job.data_urls and not HAVE_PIL:
        c.log("[비전] Pillow 미설치 → 다운스케일 없이 원본 전송. `pip install pillow` 권장")


async def _run_ocr(c, job):
    """data_urls 를 장마다 OCR → job.ocr_texts. 판독 실패면 job.skip_local=True."""
    if not job.data_urls:
        return
    if not _ocr_enabled():
        job.skip_local = True
        job.ocr_note = f"OCR 꺼짐({OCR_STATE.disabled_reason or 'OCR_ENABLE=false'})"
        return
    from summarize import ocr
    nid = job.notice["id"]
    t0 = time.time()
    texts, errs = [], []
    wsum = wn = 0.0                         # 신뢰도 글자수 가중합(공지 단위 판정)
    for k, du in enumerate(job.data_urls, 1):
        try:
            txt, conf, n = await asyncio.wait_for(asyncio.to_thread(ocr.ocr_one, du), config.OCR_TIMEOUT)
            texts.append(txt or "")
            wsum += conf * n
            wn += n
            OCR_STATE.consec_fail = 0
        except ocr.OCRUnavailable as e:
            _disable_ocr(c, str(e))
            texts.append("")
            errs.append(str(e))
            break
        except asyncio.TimeoutError:
            # 스레드는 계속 돌 수 있다(ocr._LOCK 이 다음 호출을 기다리게 함). 시간초과도 엔진 이상으로 센다.
            texts.append("")
            errs.append(f"{k}장 시간초과({config.OCR_TIMEOUT}s)")
            OCR_STATE.consec_fail += 1
        except Exception as e:
            texts.append("")
            errs.append(f"{k}장 {type(e).__name__}: {str(e)[:100]}")
            OCR_STATE.consec_fail += 1
        if OCR_STATE.consec_fail >= config.OCR_MAX_CONSEC_FAIL:
            _disable_ocr(c, f"OCR 연속 {OCR_STATE.consec_fail}회 실패: {errs[-1]}")
            break
    total = sum(len(t.strip()) for t in texts)
    conf = wsum / wn if wn else 0.0
    job.ocr_texts = texts
    c.log(f"[OCR] id={nid} {len(job.data_urls)}장 · {time.time() - t0:.1f}s · {total}자 · 신뢰도 {conf:.2f} · "
          f"백엔드 {ocr.backend_used() or '-'}" + (f" · 오류 {'; '.join(errs)}" if errs else ""))
    why = None
    if total < config.OCR_MIN_CHARS:
        why = f"{total}자 < OCR_MIN_CHARS {config.OCR_MIN_CHARS}"
    elif conf < config.OCR_MIN_SCORE:
        why = f"신뢰도 {conf:.2f} < OCR_MIN_SCORE {config.OCR_MIN_SCORE:.2f}(디자인 포스터 등 깨진 글자)"
    if why:
        job.skip_local = True
        job.ocr_note = f"OCR 판독 실패({why})" + (f" · {'; '.join(errs)}" if errs else "")
        c.log(f"[OCR 폴백] id={nid} {job.ocr_note}")


async def stage_images(c, job):
    if job.images and (config.LLM_VISION or _ocr_enabled() or getattr(c.summarizer, "cloud", None)):
        await _prepare_images(c, job)
        await _run_ocr(c, job)


# ── 단계 2: LLM 요약 → DB → 디스코드 ────────────────────────────────────
async def stage_llm(c, job):
    notice, dept = job.notice, job.dept
    nid = notice["id"]
    data_urls = job.data_urls
    kb = sum(len(u) for u in data_urls) * 3 // 4 // 1024 if data_urls else 0
    ocr_chars = sum(len(t) for t in job.ocr_texts) if job.ocr_texts else 0
    # 시작 로그: 프로세스가 요약 도중 죽으면(LLM 서버 OOM 동반) 이 줄이 마지막 흔적이 된다.
    c.log(f"[요약 시작] id={nid} {dept.get('name_ko') or notice['dept_id']} :: "
          f"{notice['title'][:40]} (본문 {len(notice.get('content_raw') or '')}자 · "
          f"이미지 {len(data_urls)}/{len(job.images)}장·{kb}KB · OCR {ocr_chars}자"
          f"{' · 로컬 생략' if job.skip_local else ''})")
    summary = engine = None
    err = None
    no_content = False
    t0 = time.time()
    kwargs = {"ocr_texts": job.ocr_texts if not job.skip_local else None}
    if job.skip_local:
        kwargs["skip_local"] = True
        kwargs["skip_reason"] = job.ocr_note
    try:
        async with c.queue.sem:
            summary, engine = await asyncio.to_thread(
                _call_summarizer, c.summarizer, notice["title"], notice.get("content_raw") or "",
                data_urls or None, kwargs)
    except EmptyContentError as e:
        no_content = True
        err = e
    except SummaryError as e:
        err = e
    except Exception as e:
        err = e

    # 결과를 (요약, 상태, 사유) 한 묶음으로 정리 → 발송·DB기록은 _publish 한 곳에서.
    img_note = f" · 이미지 {len(data_urls)}/{len(job.images)}장·{kb}KB" if job.images else ""
    if summary:
        status, reason = "done", None
        c.log(f"[요약 완료] {engine} · {time.time() - t0:.1f}s · {len(summary)}자 :: "
              f"{notice['title'][:40]}{img_note}")
    elif no_content:
        # 제목만 있고 본문·이미지 모두 없음 → LLM에 안 보냄. '요약할 내용이 없습니다' 표기(재시도 X).
        # 실패가 아니므로 디버그 발송 안 함. 사유는 DB(fail_reason)에만 기록(사후 분석용).
        status, reason = "no_content", "본문·이미지 없음 또는 이미지 로드 실패"
        c.log(f"[내용 없음] {notice['title'][:40]} (본문·이미지 모두 비어 LLM 호출 안 함)")
    else:
        # 요약 실패 = 요약만 포기. 공지 자체는 실패 문구를 달고 그대로 간다(누락 0).
        status, reason = "summary_failed", str(err)[:500]
        c.log(f"[요약 실패] {time.time() - t0:.1f}s :: {notice['title'][:40]}{img_note}\n"
              f"          사유: {err}")
    ocr_text = _ocr_text_for_db(job.ocr_texts)
    await _publish(c, notice, dept, summary, engine, status, reason, ocr_text=ocr_text)


def _call_summarizer(summarizer, title, html, image_urls, kwargs):
    """폴백 요약기(FallbackSummarizer)는 skip_local·클라우드 비전을 안다. 단일 로컬 요약기(클라우드 키 없음)는
    모른다 → 아는 인자만 넘기고, 로컬 비전이 꺼져 있으면 이미지를 빼고 보낸다(텍스트 전용 서버가 거부함)."""
    import inspect
    params = inspect.signature(summarizer.summarize).parameters
    kw = {k: v for k, v in kwargs.items() if k in params}
    if not hasattr(summarizer, "cloud") and not config.LLM_VISION:
        image_urls = None
    return summarizer.summarize(title, html, image_urls, **kw)


def _ocr_text_for_db(texts):
    if not texts:
        return None
    joined = "\n\n".join(f"[이미지 {i}]\n{t.strip()}" for i, t in enumerate(texts, 1) if t.strip())
    return joined or None


# ── 순차 경로(drain·redo·재요약 스크립트) ─────────────────────────────────
async def summarize_one(c, notice_id: int):
    """단일 공지를 처음부터 끝까지(이미지·OCR → LLM → 발송) 순차 처리."""
    job = await _load_job(c, notice_id)
    if job is None:
        return
    await stage_images(c, job)
    await stage_llm(c, job)


async def _publish(c, notice, dept, summary, engine, status, fail_reason, ocr_text=None):
    """요약 결과를 DB에 쓰고 사용자에게 보여 준다 — 발송(요약 뒤 1회) 또는 기존 메시지 수정(2단).

    SUMMARY_FAIL_NOTE('요약을 실패하였습니다')가 보이는 순간의 감시채널 디버그는 두 경로 모두
    notifier가 책임진다(send_summarized / edit_summary) → 어떤 경로로 실패하든 알림이 빠지지 않는다.
    """
    nid = notice["id"]
    # 이미 메시지가 나가 있으면(2단 모드 또는 발송 후 재적재) 수정, 아니면 지금 발송.
    deferred = (send_after_summary(c) and c.notifier.send_enabled
                and not notice.get("discord_message_id"))

    if not deferred:
        await asyncio.to_thread(c.store.set_summary, nid, summary, engine, ocr_text, status, fail_reason)
        fresh = await asyncio.to_thread(c.store.get_notice, nid)
        try:
            await asyncio.to_thread(c.notifier.edit_summary,
                                    fresh.get("discord_channel_id"), fresh.get("discord_message_id"),
                                    fresh, dept)
        except Exception as e:
            c.log(f"[edit 실패] {notice['title'][:30]}: {e}")
        return

    # 요약 뒤 발송: 상태는 '미완(summarizing)'으로 두고 요약만 저장한다.
    #   여기서 죽으면 다음 부팅 때 재적재되고, 요약이 있으니 LLM 없이 발송만 다시 시도한다.
    await asyncio.to_thread(c.store.set_summary, nid, summary, engine, ocr_text, "summarizing", fail_reason)
    out = dict(await asyncio.to_thread(c.store.get_notice, nid))
    out["status"] = status                  # 임베드는 최종 상태로 그린다(실패/내용없음 문구 포함)
    try:
        channel_id, message_id = await asyncio.to_thread(c.notifier.send_summarized, out, dept)
    except Exception as e:
        # 공지를 '미완'으로 남겨 둔다 → 다음 부팅/재적재 때 다시 발송(요약은 재사용). 누락되지 않는다.
        c.log(f"[발송 실패] {notice['title'][:40]}: {e}\n"
              f"          → 요약은 저장됨. 이 공지는 '미완'으로 남아 다음 부팅 때 발송을 다시 시도한다")
        try:
            c.notifier.debug(f"**공지 발송 실패** · {dept.get('name_ko') or notice.get('dept_id') or '-'}\n"
                             f"{(notice.get('title') or '')[:80]}\n"
                             f"{errors.describe(e)}\n"
                             f"요약은 저장됨 — 다음 부팅 때 발송을 다시 시도합니다")
        except Exception as de:
            c.log(f"[debug 전송 실패] {de}")
        return
    await asyncio.to_thread(c.store.set_notified, nid, channel_id, message_id)
    await asyncio.to_thread(c.store.set_summary, nid, summary, engine, None, status, fail_reason)


def _debug_unexpected(c, where, notice_id, e):
    """요약 처리 단계가 통째로 터진 경우(예상 밖 버그). 공지는 'summarizing'으로 남아 다음 부팅
    재적재 때 재시도되지만, 그때까지 요약 없는 빈 임베드로 방치되므로 즉시 알린다."""
    c.log(f"[{where} 예외] notice={notice_id}\n{errors.full(e)}")
    try:
        c.notifier.debug(f"**요약 처리 중 예상 밖 오류** ({where}) · notice_id={notice_id}\n{errors.describe(e)}")
    except Exception as de:
        c.log(f"[debug 전송 실패] {de}")


# ── 상시 운영: router + OCR 워커 1 + LLM 워커 N ───────────────────────────
async def _router_loop(c, ocr_q, llm_q):
    """intake 큐(공지 id)를 받아 이미지 유무로 OCR 큐 / LLM 큐에 나눈다. 인터럽트식(큐가 깨운다)."""
    while True:
        notice_id = await c.queue.get()
        try:
            job = await _load_job(c, notice_id)
            if job is not None:
                if job.images and (config.LLM_VISION or _ocr_enabled() or getattr(c.summarizer, "cloud", None)):
                    ocr_q.put_nowait(job)
                else:
                    llm_q.put_nowait(job)
        except Exception as e:
            _debug_unexpected(c, "router", notice_id, e)
        finally:
            c.queue.task_done()


async def _ocr_loop(c, ocr_q, llm_q):
    """이미지 공지: 내려받기 + OCR(직렬, GPU 1개) → LLM 큐. 오래 놀면 OCR 엔진 반납."""
    idle = config.OCR_IDLE_RELEASE_SEC
    while True:
        try:
            job = await (asyncio.wait_for(ocr_q.get(), idle) if idle > 0 else ocr_q.get())
        except asyncio.TimeoutError:
            from summarize import ocr
            await asyncio.to_thread(ocr.release, idle)
            continue
        try:
            await stage_images(c, job)
        except Exception as e:
            _debug_unexpected(c, "ocr", job.notice["id"], e)
            job.skip_local = True               # 이미지 단계가 터져도 공지는 요약으로 보낸다(누락 0)
            job.ocr_note = f"OCR 단계 예외: {type(e).__name__}"
        llm_q.put_nowait(job)
        ocr_q.task_done()


async def _llm_loop(c, llm_q):
    while True:
        job = await llm_q.get()
        try:
            await stage_llm(c, job)
        except Exception as e:
            _debug_unexpected(c, "llm", job.notice["id"], e)
        finally:
            llm_q.task_done()


def start_workers(c):
    """상시 운영 워커 기동. 반환: 태스크 목록(router 1 + OCR 1 + LLM LLM_MAX_CONCURRENCY)."""
    ocr_q, llm_q = asyncio.Queue(), asyncio.Queue()
    tasks = [asyncio.create_task(_router_loop(c, ocr_q, llm_q), name="router"),
             asyncio.create_task(_ocr_loop(c, ocr_q, llm_q), name="ocr")]
    tasks += [asyncio.create_task(_llm_loop(c, llm_q), name=f"llm{i}")
              for i in range(config.LLM_MAX_CONCURRENCY)]
    return tasks


async def worker_loop(c):
    """(구) 단일 워커: intake 큐를 순차 처리. 테스트·호환용 — 상시 운영은 start_workers."""
    while True:
        notice_id = await c.queue.get()
        try:
            await summarize_one(c, notice_id)
        except Exception as e:
            _debug_unexpected(c, "worker", notice_id, e)
        finally:
            c.queue.task_done()


async def drain(c):
    """run_once용: 큐가 빌 때까지 순차 처리."""
    while not c.queue.empty():
        notice_id = await c.queue.get()
        try:
            await summarize_one(c, notice_id)
        except Exception as e:
            _debug_unexpected(c, "drain", notice_id, e)
        finally:
            c.queue.task_done()
