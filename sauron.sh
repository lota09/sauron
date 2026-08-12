#!/usr/bin/env bash
# sauron.sh — sauron 상시 운영 실행/중지/상태.
#   로그는 main(크롤러)·discord_bot(봇)이 logs/ 에 '일자별 회전'으로 남긴다(core/log.py).
#   → 이 스크립트는 redirect 없이 실행. 프로세스는 nohup+disown 으로 완전 분리되어,
#     `bash sauron.sh` 만 하면(앞에 nohup … & 안 붙여도) 바로 프롬프트로 돌아오고 로그아웃해도 계속 돈다.
#
#   bash sauron.sh            크롤러 + 구독봇 백그라운드 시작(기본)
#   bash sauron.sh --nobot    봇 없이 크롤러만
#   bash sauron.sh stop
#   bash sauron.sh restart [--nobot]
#   bash sauron.sh status
#
#   실행 비트가 없으면 그냥 `bash sauron.sh …` (또는 chmod +x sauron.sh).
#   venv 이름이 .venv가 아니면: VENV_DIR=myenv bash sauron.sh
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR="${VENV_DIR:-.venv}"
PY="./$VENV_DIR/bin/python"
PID_DIR=".run"
mkdir -p "$PID_DIR" logs

_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

_start() {                       # _start <name> <python args...>
  local name="$1"; shift
  local pidf="$PID_DIR/$name.pid"
  if _alive "$pidf"; then echo "[$name] 이미 실행 중 (pid $(cat "$pidf"))"; return; fi
  if [ ! -x "$PY" ]; then echo "가상환경 파이썬 없음: $PY  → deploy.py 를 먼저 실행하세요"; exit 1; fi
  # nohup(로그아웃 SIGHUP 무시) + disown(셸 잡목록 제거) + stdin 차단 = 완전 분리, 즉시 복귀
  nohup "$PY" "$@" </dev/null >/dev/null 2>&1 &
  local pid=$!
  disown "$pid" 2>/dev/null || true
  echo "$pid" > "$pidf"
  echo "[$name] 시작 (pid $pid)"
}

_stop() {                        # _stop <name>
  local name="$1"; local pidf="$PID_DIR/$name.pid"
  if _alive "$pidf"; then kill "$(cat "$pidf")" && echo "[$name] 종료 (pid $(cat "$pidf"))";
  else echo "[$name] 실행 아님"; fi
  rm -f "$pidf"
}

_status() {                      # _status <name>
  local name="$1"; local pidf="$PID_DIR/$name.pid"
  if _alive "$pidf"; then echo "[$name] 🟢 실행 중 (pid $(cat "$pidf"))"; else echo "[$name] 🔴 정지"; fi
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
  --nobot) # `sauron.sh --nobot` = start --nobot
    _start crawler main.py run --dst poly
    echo "로그: logs/sauron.log — 자정마다 회전" ;;
  *) echo "사용: bash sauron.sh {start|stop|restart|status} [--nobot]"; exit 1 ;;
esac
