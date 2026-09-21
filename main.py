# -*- coding: utf-8 -*-
"""
main.py — 진입점.

  모드(무엇을):
    run          상시: 워커 + 크롤 스케줄 루프 (기본)
    once         1회: (재적재+)크롤 1회 처리 후 종료. cron에 걸면 실서비스 근사
    redo N       임의 N개 학과 최신 공지 강제 재처리(크롤 X)
                 옵션: --depts a,b (이 학과들에서만) · --exclude a,b (이 학과 제외)
                       --per K (학과당 K건) · --random (1페이지에서 임의로, 없으면 최신)
    query "검색어" 제목 검색 → 선택 → 재처리 또는 DB에서 제거
    check        학과별 수집 점검(목록 → 최신 공지 상세). 발송·DB 기록 없음. --depts a,b 로 일부만

  --dst (어디로 보낼지, 택1, 기본 null):
    null(기본)   전송 안 함(=구 dryrun)
    mono         통합채널(setup_guild가 생성, DB app_meta) 하나로 몰빵
    poly         각 학과 전용 채널로(+@everyone)
    <채널ID>     명시한 단일 채널로

  --nosummary  요약(+상세fetch) 생략. --dst null 과 함께면 순수 시딩, 발송 대상과 함께면 제목+링크만 발송.

  예)
    python main.py once --dst null --nosummary   # 시딩(구 init)
    python main.py once --dst poly               # 각 채널로 최신공지 처리 (cron=실서비스 근사)
    python main.py run  --dst poly               # 상시 운영
    python main.py redo 4 --dst mono             # 임의 4개 최신공지를 통합채널로
    python main.py redo 4 --depts job_empl,ssupath --per 2 --random --dst mono   # 지정 학과에서 2건씩 임의로
    python main.py query "수강신청" --dst mono    # 검색·선택 후 재처리
"""
import asyncio
import sys

import config
from core.log import setup_logger
from core.runstatus import record_start, beat
from core.queue import WorkQueue
from crawl.fetcher import Fetcher
from db.store import Store
from notify.notifier import Notifier
from pipeline import Components, crawl_pass, run_once
from summarize.llm import default_summarizer
from summarize.worker import worker_loop


def build_components(logger=None, dst="null", nosummary=False):
    logger = logger or setup_logger()
    store = Store(config.DB_PATH)
    summarizer = default_summarizer()
    try:
        model = summarizer.ensure_model()
        logger.info(f"[LLM] 사용 모델: {model} @ {config.LLM_BASE_URL}")
    except Exception as e:
        logger.info(f"[LLM] 모델 확인 실패({e}) → {summarizer.model}")
    # 통합·감시 채널ID는 DB(app_meta)에서만 읽는다 — setup_guild가 생성·저장한 값.
    dbg_ch = store.get_meta("debug_channel_id")
    mono_ch = store.get_meta("mono_channel_id")
    notifier = Notifier(logger, dst=dst, debug_channel_id=dbg_ch, mono_channel_id=mono_ch)
    # 채널ID는 값 자체를 남긴다 — '설정/없음'만 남기면 나중에 "어디로 보냈나"를 로그로 못 따라간다.
    logger.info(f"[전송] dst={notifier.dst} dry={notifier.dry} nosummary={nosummary} "
                f"통합채널={mono_ch or '없음'} 감시채널={dbg_ch or '없음'}")
    if not dbg_ch:
        # 감시채널이 없으면 모든 디버그(요약 실패 포함)가 디스코드로 안 나간다 — 시작 시 한 번 못박는다.
        logger.info("[경고] 감시채널ID 없음(app_meta.debug_channel_id) → 디버그·요약실패 알림이 "
                    "디스코드로 나가지 않고 로그에만 남습니다. `python -m notify.setup_guild` 필요")
    return Components(
        store=store,
        fetcher=Fetcher(),
        summarizer=summarizer,
        notifier=notifier,
        queue=WorkQueue(),
        logger=logger,
        nosummary=nosummary,
    )


async def _run_forever(c):
    n = c.queue.requeue_pending(c.store)
    if n:
        c.log(f"[부팅 재적재] 미완 요약 {n}건")
    workers = [asyncio.create_task(worker_loop(c)) for _ in range(config.LLM_MAX_CONCURRENCY)]
    c.log(f"[start] 워커 {len(workers)}개 · 크롤주기 {config.CRAWL_INTERVAL_SEC}s · LLM {config.LLM_BASE_URL}")
    record_start(c.store)          # 상태확인용: run_pid·시작시각 기록
    try:
        while True:
            new_n = 0
            try:
                new_n = await crawl_pass(c)
            except Exception as e:
                c.log(f"[crawl_pass 예외] {e}")
                c.notifier.debug(f"crawl_pass 예외: {e}")
            beat(c.store, new_n)   # 상태확인용: heartbeat 갱신(+직전 신규 건수)
            c.store.checkpoint()
            await asyncio.sleep(config.CRAWL_INTERVAL_SEC)
    finally:
        for w in workers:
            w.cancel()


# 값을 하나 받는 옵션(그 다음 토큰은 모드·N·검색어로 해석하지 않는다)
_VALUE_OPTS = ("--dst", "--depts", "--exclude", "--per")


def _redo_opts(argv):
    """redo 전용 옵션 → {'depts': [..]|None, 'exclude': [..], 'per': int, 'random': bool}."""
    o = {"depts": None, "exclude": [], "per": 1, "random": "--random" in argv}
    for i, a in enumerate(argv):
        for key in ("depts", "exclude", "per"):
            flag = f"--{key}"
            val = a.split("=", 1)[1] if a.startswith(flag + "=") else (
                argv[i + 1] if a == flag and i + 1 < len(argv) else None)
            if val is None:
                continue
            if key == "per":
                o["per"] = max(1, int(val))
            else:
                o[key] = [x.strip() for x in val.split(",") if x.strip()]
    return o


def _parse_args(argv):
    """모드 + (redo)정수/(query)검색어 + --dst VALUE + --nosummary.
    반환: (mode, num, query, dst, nosummary). redo 전용 옵션은 _redo_opts."""
    mode = "run"
    num = None
    query = None
    dst = "null"
    nosummary = False
    expect = None
    for a in argv[1:]:
        if expect:
            if expect == "--dst":
                dst = a
            expect = None
        elif a in _VALUE_OPTS:
            expect = a
        elif a in ("run", "once", "redo", "query", "check"):
            mode = a
        elif a == "--nosummary":
            nosummary = True
        elif a.startswith("--dst="):
            dst = a.split("=", 1)[1]
        elif a.isdigit():
            num = int(a)
        elif not a.startswith("-"):
            query = a          # redo/query 인자(원문 보존)
    return mode, num, query, dst, nosummary


def main():
    mode, num, query, dst, nosummary = _parse_args(sys.argv)
    c = build_components(dst=dst, nosummary=nosummary)
    try:
        if mode == "once":
            asyncio.run(run_once(c))
        elif mode == "redo":
            from devtools import debug_resummarize
            asyncio.run(debug_resummarize(c, 10 if num is None else num,  # 명시적 0은 0으로 존중
                                          **_redo_opts(sys.argv[1:])))
        elif mode == "check":
            from devtools import check_depts
            asyncio.run(check_depts(c, _redo_opts(sys.argv[1:])["depts"]))
        elif mode == "query":
            if not query:
                c.log('query 인자 필요: python main.py query "검색어"')
            else:
                from devtools import query_notices
                asyncio.run(query_notices(c, query))
        else:
            try:
                asyncio.run(_run_forever(c))
            except KeyboardInterrupt:
                c.log("종료")
    except Exception as e:
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
        request_shutdown()
        c.store.close()


if __name__ == "__main__":
    main()
