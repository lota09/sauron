# -*- coding: utf-8 -*-
"""core/runstatus.py — 크롤러(main.py run)의 생존/하트비트 상태를 app_meta에 기록·조회.

크롤러(쓰기)와 봇(읽기)이 같은 머신·같은 DB를 공유한다는 가정.
  · record_start(store): 시작 시 run_pid + run_started_at 기록.
  · beat(store, n):      매 크롤 주기마다 run_heartbeat(+직전 신규 건수) 갱신.
  · read_status(store, stale_sec): 3단계 상태(running/stale/stopped) + 부가정보.

PID '지금 존재?'는 os.kill(pid,0)로 실시간 확인. PID 재사용 오탐은 '시작 시각 대조'로 막는다
  — /proc 의 실제 프로세스 시작 wall-clock 이 저장된 run_started_at과 근사해야 '우리 프로세스'.
  (비-리눅스 등 /proc 없으면 대조 불가 → PID 생존만 신뢰.)
"""
import os
import time

K_PID = "run_pid"
K_STARTED = "run_started_at"
K_BEAT = "run_heartbeat"
K_LASTNEW = "run_last_new"

_START_TOL = 5   # /proc 시작시각 대조 허용오차(초)


def record_start(store):
    now = f"{time.time():.0f}"
    store.set_meta(K_PID, str(os.getpid()))
    store.set_meta(K_STARTED, now)
    store.set_meta(K_BEAT, now)


def beat(store, last_new=None):
    store.set_meta(K_BEAT, f"{time.time():.0f}")
    if last_new is not None:
        store.set_meta(K_LASTNEW, str(last_new))


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # 존재하지만 시그널 권한 없음 = 살아있음
    except OSError:
        return False
    return True


def _proc_start_epoch(pid):
    """/proc 기반 프로세스 실제 시작 wall-clock epoch. 실패(비리눅스 등) 시 None."""
    try:
        clk = os.sysconf("SC_CLK_TCK")
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            after = f.read().rsplit(")", 1)[1].split()   # comm에 공백·괄호 가능 → 마지막 ')' 뒤부터
        starttime_ticks = float(after[19])               # field 22(starttime); state=after[0]부터라 22→[19]
        with open("/proc/stat", encoding="utf-8") as f:
            btime = next(int(l.split()[1]) for l in f if l.startswith("btime"))
        return btime + starttime_ticks / clk
    except Exception:
        return None


def _is_ours(pid, started_at):
    """PID 재사용 오탐 방지: /proc 실제 시작시각이 저장 run_started_at과 근사하면 우리 프로세스."""
    ps = _proc_start_epoch(pid)
    if ps is None or started_at is None:
        return True          # 대조 불가 → PID 생존만 신뢰
    return abs(ps - started_at) <= _START_TOL


def read_status(store, stale_sec):
    """반환: {state, pid, alive, fresh, uptime, since_beat, last_new}.
    state = 'running'(살아있고 최신) | 'stale'(살아있으나 heartbeat 낡음=멈춤/붕괴 의심) | 'stopped'."""
    now = time.time()
    pid_s = store.get_meta(K_PID)
    started = store.get_meta(K_STARTED)
    beat_s = store.get_meta(K_BEAT)
    last_new = store.get_meta(K_LASTNEW)

    pid = int(pid_s) if (pid_s and pid_s.isdigit()) else None
    started = float(started) if started else None
    beat_v = float(beat_s) if beat_s else None

    if pid is None or beat_v is None:
        return {"state": "stopped", "pid": None, "alive": False, "fresh": False,
                "uptime": None, "since_beat": None, "last_new": last_new}

    alive = _pid_alive(pid) and _is_ours(pid, started)
    fresh = (now - beat_v) <= stale_sec
    state = "running" if (alive and fresh) else ("stale" if alive else "stopped")
    return {
        "state": state, "pid": pid, "alive": alive, "fresh": fresh,
        "uptime": (now - started) if started else None,
        "since_beat": now - beat_v,
        "last_new": last_new,
    }
