# -*- coding: utf-8 -*-
"""
main.py — 진입점.

  python main.py init   # 부트스트랩: 부팅 재적재 + 1회 크롤 + 요약 드레인 (첫 실행=시딩, 이후=밀린 신규 처리)
  python main.py run    # 상시: 워커 + 10분 스케줄 루프 (기본)
  python main.py redo N # 임의 N개 학과 최신 공지 재요약(크롤 X). 프롬프트/품질 튜닝용

라우팅 플래그(직교): --prod(실서비스) · --mono(통합채널 몰빵) · --dryrun(전송안함).
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
from pipeline import Components, crawl_pass, run_once, seed_all
from summarize.llm import default_summarizer, ClovaSummarizer
from summarize.ocr import get_ocr
from summarize.worker import worker_loop


def build_components(logger=None, dry_run=None, mono=None):
    logger = logger or setup_logger()
    store = Store(config.DB_PATH)
    summarizer = default_summarizer()
    # 시작 시 1회: 모델 자동감지('auto') → 확정. run 모드면 프로세스당 한 번.
    try:
        model = summarizer.ensure_model()
        logger.info(f"[LLM] 사용 모델: {model} @ {config.LLM_BASE_URL}")
    except Exception as e:
        logger.info(f"[LLM] 모델 확인 실패({e}) → {summarizer.model}")
    notifier = Notifier(logger, dry_run=dry_run, mono=mono)   # debug_mode 는 config.DEBUG_EN
    logger.info(f"[전송] dry_run={notifier.dry} DEBUG_EN={notifier.debug_mode} mono={notifier.mono} "
                f"({'몰빵채널' if notifier.mono else ('가짜서버·학과채널' if notifier.debug_mode else '실서비스·학과채널')})")
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
    """모드(init/run/redo) + 정수 인자 + 플래그(--dryrun/--debug/--prod/--mono).
    예: 'redo 10' / 'redo 10 --dryrun' / 'run --mono' / 'init'."""
    mode = "run"
    num = None
    query = None
    dryrun_flag = False
    debug_flag = False
    prod_flag = False
    mono_flag = False
    for a in argv[1:]:
        bare = a.lstrip("-").lower()
        is_flag = a.startswith("-")
        if not is_flag and bare in ("init", "run", "redo", "debug"):
            mode = "redo" if bare == "debug" else bare   # 'debug'는 'redo' 별칭(호환)
        elif bare.isdigit():
            num = int(bare)
        elif bare == "dryrun":
            dryrun_flag = True
        elif is_flag and bare == "debug":
            debug_flag = True
        elif is_flag and bare == "prod":
            prod_flag = True
        elif is_flag and bare == "mono":
            mono_flag = True
        elif not is_flag:
            query = a       # redo 검색어(원문 보존): 예 redo "수강신청"
    return mode, num, query, dryrun_flag, debug_flag, prod_flag, mono_flag


def main():
    mode, num, query, dryrun_flag, debug_flag, prod_flag, mono_flag = _parse_args(sys.argv)
    # 라우팅은 오직 플래그로: 기본 가짜서버(안전), --prod 만 실서비스. config.json은 무시.
    config.DEBUG_EN = not prod_flag
    dry = dryrun_flag                   # dry-run 은 오직 --dryrun 플래그
    c = build_components(dry_run=dry, mono=mono_flag)   # --mono 면 통합채널 몰빵
    try:
        if mode == "init":
            asyncio.run(seed_all(c))    # 목록만 긁어 시딩(무발송·무요약)
        elif mode == "redo":
            if query:
                from devtools import redo_search
                asyncio.run(redo_search(c, query))     # 검색→선택→재요약
            else:
                from devtools import debug_resummarize
                asyncio.run(debug_resummarize(c, 10 if num is None else num))  # 명시적 0은 0으로 존중
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
