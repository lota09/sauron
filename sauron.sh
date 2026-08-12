#!/usr/bin/env bash
# sauron.sh — sauron 상시 운영 실행/중지/상태.
#   로그는 main(크롤러)·discord_bot(봇)이 logs/ 에 '일자별 회전'으로 남긴다(core/log.py).
#   → redirect 없이 실행. nohup+disown 으로 완전 분리되어 `bash sauron.sh` 만 하면 바로 복귀·계속 실행.
#   프로세스 탐지는 pid 파일 + pgrep(패턴) 둘 다 → 손으로 띄운 것도 stop/status가 잡는다.
#
#   bash sauron.sh            크롤러 + 구독봇 백그라운드 시작(기본)
#   bash sauron.sh --nobot    봇 없이 크롤러만
#   bash sauron.sh stop
#   bash sauron.sh restart [--nobot]
#   bash sauron.sh status
#
#   실행 비트 없으면 `bash sauron.sh …`. venv 이름 다르면 VENV_DIR=myenv bash sauron.sh
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR="${VENV_DIR:-.venv}"
PY="./$VENV_DIR/bin/python"
PID_DIR=".run"
mkdir -p "$PID_DIR" logs

_pat_of() { case "$1" in crawler) echo "main\.py run";; bot) echo "notify\.discord_bot";; esac; }

_pids() {                        # <name> → 실행 중 PID들(pid파일 + pgrep 패턴, 수동 실행 포함)
  local name="$1" pidf="$PID_DIR/$1.pid" ids=""
  [ -f "$pidf" ] && kill -0 "$(cat "$pidf" 2>/dev/null)" 2>/dev/null && ids="$(cat "$pidf")"
  ids="$ids $(pgrep -f "$(_pat_of "$name")" 2>/dev/null || true)"
  echo $ids | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' '
}

_start() {                       # <name> <python args...>
  local name="$1"; shift
  local run; run="$(_pids "$name")"
  if [ -n "$run" ]; then echo "[$name] 이미 실행 중 (pid $run)"; return; fi
  if [ ! -x "$PY" ]; then echo "가상환경 파이썬 없음: $PY  → deploy.py 를 먼저 실행하세요"; exit 1; fi
  nohup "$PY" "$@" </dev/null >/dev/null 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  echo "$pid" > "$PID_DIR/$name.pid"
  echo "[$name] 시작 (pid $pid)"
}

_stop() {                        # <name>
  local name="$1"; local run; run="$(_pids "$name")"
  if [ -n "$run" ]; then kill $run 2>/dev/null && echo "[$name] 종료 (pid $run)";
  else echo "[$name] 실행 아님"; fi
  rm -f "$PID_DIR/$name.pid"
}

_status() {                      # <name>
  local name="$1"; local run; run="$(_pids "$name")"
  if [ -n "$run" ]; then echo "[$name] 🟢 실행 중 (pid $run)"; else echo "[$name] 🔴 정지"; fi
}

cmd="${1:-start}"; shift || true
with_bot=true; [ "${1:-}" = "--nobot" ] && with_bot=false

case "$cmd" in
  start)
    _start crawler main.py run --dst poly
    $with_bot && _start bot -m notify.discord_bot
    echo "로그: logs/sauron.log (크롤러) · logs/bot.log (봇) — 자정마다 회전"
    ;;
  stop)    _stop crawler; _stop bot ;;
  restart)
    _stop crawler; _stop bot; sleep 1
    _start crawler main.py run --dst poly
    $with_bot && _start bot -m notify.discord_bot ;;
  status)  _status crawler; _status bot ;;
  --nobot)
    _start crawler main.py run --dst poly
    echo "로그: logs/sauron.log — 자정마다 회전" ;;
  *) echo "사용: bash sauron.sh {start|stop|restart|status} [--nobot]"; exit 1 ;;
esac
