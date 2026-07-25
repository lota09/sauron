# -*- coding: utf-8 -*-
"""
main.py — 진입점.

  python main.py once   # 부팅 재적재 + 1회 크롤 + 요약 드레인 (테스트/수동)
  python main.py run    # 상시: 워커 + 10분 스케줄 루프 (기본)

단일 asyncio 프로세스. 요약 LLM은 config.LLM_BASE_URL(개발 LAN / 배포 localhost).
"""
import asyncio
import sys

import config
from core.log import setup_logger
from core.queue import WorkQueue
from crawl.fetcher import Fetcher
from db.store import Store
from notify.notifier import Notifier
from pipeline import Components, crawl_pass, run_once
from summarize.llm import default_summarizer, ClovaSummarizer
from summarize.ocr import get_ocr
from summarize.worker import worker_loop


def build_components(logger=None, dry_run=None):
    logger = logger or setup_logger()
    store = Store(config.DB_PATH)
    summarizer = default_summarizer()
    # 시작 시 1회: 모델 자동감지('auto') → 확정. run 모드면 프로세스당 한 번.
    try:
        model = summarizer.ensure_model()
        logger.info(f"[LLM] 사용 모델: {model} @ {config.LLM_BASE_URL}")
    except Exception as e:
        logger.info(f"[LLM] 모델 확인 실패({e}) → {summarizer.model}")
    notifier = Notifier(logger, dry_run=dry_run)   # debug_mode 는 config.DEBUG_EN
    logger.info(f"[전송] dry_run={notifier.dry} DEBUG_EN={notifier.debug_mode} "
                f"({'가짜채널' if notifier.debug_mode else '실채널'})")
    return Components(
        store=store,
        fetcher=Fetcher(),
        ocr=get_ocr(),
        summarizer=summarizer,
        notifier=notifier,
        queue=WorkQueue(),
        clova=ClovaSummarizer(),
        logger=logger,
    )


async def _run_forever(c):
    n = c.queue.requeue_pending(c.store)
    if n:
        c.log(f"[부팅 재적재] 미완 요약 {n}건")
    workers = [asyncio.create_task(worker_loop(c)) for _ in range(config.LLM_MAX_CONCURRENCY)]
    c.log(f"[start] 워커 {len(workers)}개 · 크롤주기 {config.CRAWL_INTERVAL_SEC}s · LLM {config.LLM_BASE_URL}")
    try:
        while True:
            try:
                await crawl_pass(c)
            except Exception as e:
                c.log(f"[crawl_pass 예외] {e}")
                c.notifier.debug(f"crawl_pass 예외: {e}")
            c.store.checkpoint()   # 상시 모드: 뷰어가 최신 상태 보게 주기적 flush
            await asyncio.sleep(config.CRAWL_INTERVAL_SEC)
    finally:
        for w in workers:
            w.cancel()


def _parse_args(argv):
    """모드(once/run/debug) + 정수 인자 + 플래그(dryrun/debug).
    예: 'debug 10' / 'debug 10 --dryrun' / 'run --debug'."""
    mode = "run"
    num = None
    dryrun_flag = False
    debug_flag = False
    prod_flag = False
    for a in argv[1:]:
        bare = a.lstrip("-").lower()
        is_flag = a.startswith("-")
        if not is_flag and bare in ("once", "run", "redo", "debug"):
            mode = "redo" if bare == "debug" else bare   # 'debug'는 'redo' 별칭(호환)
        elif bare.isdigit():
            num = int(bare)
        elif bare == "dryrun":
            dryrun_flag = True
        elif is_flag and bare == "debug":
            debug_flag = True
        elif is_flag and bare == "prod":
            prod_flag = True
    return mode, num, dryrun_flag, debug_flag, prod_flag


def main():
    mode, num, dryrun_flag, debug_flag, prod_flag = _parse_args(sys.argv)
    # 라우팅은 오직 플래그로: 기본 가짜채널(안전), --prod 만 실채널. config.json은 무시.
    config.DEBUG_EN = not prod_flag
    dry = dryrun_flag                   # dry-run 은 오직 --dryrun 플래그
    c = build_components(dry_run=dry)
    try:
        if mode == "once":
            asyncio.run(run_once(c))
        elif mode == "redo":
            from devtools import debug_resummarize
            asyncio.run(debug_resummarize(c, num or 10))
        else:
            try:
                asyncio.run(_run_forever(c))
            except KeyboardInterrupt:
                c.log("종료")
    except Exception as e:
        # 잡히지 않은 런타임 에러 → 감시채널로 디버그 메시지 후 재전파
        import traceback
        tb = traceback.format_exc()
        c.log(f"[치명적 오류] {e}")
        try:
            c.notifier.debug(f"치명적 오류로 종료됨:\n{e}\n```\n{tb[-1500:]}\n```")
        except Exception as de:
            c.log(f"[debug 전송 실패] {de}")
        raise
    finally:
        from summarize.llm import request_shutdown
        request_shutdown()   # 진행 중 LLM 스트림 중단
        c.store.close()      # WAL 체크포인트 → notice.db 본 파일에 반영


if __name__ == "__main__":
    main()
