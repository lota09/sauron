# -*- coding: utf-8 -*-
"""core/runstatus.py — 크롤러(main.py run) 상태 조회.

생존 판단은 '프로세스 패턴'(pgrep -f)으로 한다 — sauron.sh와 동일 방식. PID/시작시각 대조가 필요 없고
PID 재사용 오탐도 없다(같은 머신 가정). app_meta의 heartbeat는 '살아있으나 멈춤(행/붕괴)' 구분용,
run_started_at은 가동시간 표시용.
  · record_start(store): 시작 시 run_started_at 기록(+heartbeat 초기화).
  · beat(store, n):      매 크롤 주기마다 heartbeat(+직전 신규 건수) 갱신.
  · read_status(store, stale_sec): running/stale/stopped + 부가정보.
"""
import subprocess
import time

K_STARTED = "run_started_at"
K_BEAT = "run_heartbeat"
K_LASTNEW = "run_last_new"

RUN_PATTERN = r"main\.py run"   # pgrep -f 패턴(크롤러). sauron.sh와 동일. once/query/봇과 안 겹침.


def record_start(store):
    now = f"{time.time():.0f}"
    store.set_meta(K_STARTED, now)
    store.set_meta(K_BEAT, now)


def beat(store, last_new=None):
    store.set_meta(K_BEAT, f"{time.time():.0f}")
    if last_new is not None:
        store.set_meta(K_LASTNEW, str(last_new))


def _proc_pids(pattern):
    """패턴에 맞는 실행 중 프로세스 PID들(pgrep -f). pgrep 없거나 실패 시 빈 리스트."""
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5)
        return [int(x) for x in r.stdout.split() if x.isdigit()]
    except Exception:
        return []


def read_status(store, stale_sec):
    """반환: {state, pid, alive, fresh, uptime, since_beat, last_new}.
    state = 'running'(프로세스 있고 heartbeat 최신) | 'stale'(프로세스는 있으나 heartbeat 낡음) | 'stopped'."""
    now = time.time()
    started = store.get_meta(K_STARTED)
    beat_s = store.get_meta(K_BEAT)
    last_new = store.get_meta(K_LASTNEW)
    started = float(started) if started else None
    beat_v = float(beat_s) if beat_s else None

    pids = _proc_pids(RUN_PATTERN)
    alive = bool(pids)
    fresh = beat_v is not None and (now - beat_v) <= stale_sec
    state = "running" if (alive and fresh) else ("stale" if alive else "stopped")
    return {
        "state": state, "pid": pids[0] if pids else None, "alive": alive, "fresh": fresh,
        "uptime": (now - started) if started else None,
        "since_beat": (now - beat_v) if beat_v is not None else None,
        "last_new": last_new,
    }
