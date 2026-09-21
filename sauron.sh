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
#   ── supervisord/systemd 용 포어그라운드(--fg | --foreground) ──────────────────
#   bash sauron.sh --fg              크롤러+봇을 자식으로 띄우고 스크립트가 죽지 않고 대기.
#                                    TERM/INT 받으면 자식 정리 후 종료, 자식 하나라도 죽으면 나머지 정리 후 그 종료코드로 exit.
#   bash sauron.sh --fg --nobot      크롤러만. exec 이라 스크립트가 python 으로 '대체'되어 시그널·종료코드가 직결된다.
#   bash sauron.sh --fg --bot        봇만(마찬가지로 exec).
#   포어그라운드에선 nohup/disown/pid파일 잠금을 쓰지 않고 stdout/stderr 도 막지 않는다(supervisor 가 수집).
#
#   supervisor 예시 — 크롤러·봇을 따로 재시작하려면 아래처럼 program 을 둘로 나누는 쪽을 권장:
#     [program:sauron-crawler]
#     command=/opt/sauron/sauron.sh --fg --nobot
#     directory=/opt/sauron
#     autostart=true
#     autorestart=true
#     stopsignal=TERM
#     stopasgroup=true
#     killasgroup=true
#
#     [program:sauron-bot]
#     command=/opt/sauron/sauron.sh --fg --bot
#     ...(동일)
#   한 program 으로 묶으려면 command=/opt/sauron/sauron.sh --fg (둘 중 하나 죽으면 같이 내려가고 통째로 재시작된다)
#
#   실행 비트 없으면 `bash sauron.sh …`. venv 이름 다르면 VENV_DIR=myenv bash sauron.sh
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR="${VENV_DIR:-.venv}"
PY="./$VENV_DIR/bin/python"
PID_DIR=".run"
mkdir -p "$PID_DIR" logs

CRAWLER_ARGS=(main.py run --dst poly)
BOT_ARGS=(-m notify.discord_bot)

_pat_of() { case "$1" in crawler) echo "main\.py run";; bot) echo "notify\.discord_bot";; esac; }

_pids() {                        # <name> → 실행 중 PID들(pid파일 + pgrep 패턴, 수동 실행 포함)
  local name="$1" pidf="$PID_DIR/$1.pid" ids=""
  [ -f "$pidf" ] && kill -0 "$(cat "$pidf" 2>/dev/null)" 2>/dev/null && ids="$(cat "$pidf")"
  ids="$ids $(pgrep -f "$(_pat_of "$name")" 2>/dev/null || true)"
  # grep이 매칭 0이면 종료코드 1 → set -e/pipefail로 스크립트가 죽으므로 반드시 || true.
  echo $ids | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' ' || true
}

_need_py() {
  [ -x "$PY" ] || { echo "가상환경 파이썬 없음: $PY  → deploy.py 를 먼저 실행하세요" >&2; exit 1; }
}

_start() {                       # <name> <python args...>
  local name="$1"; shift
  local run; run="$(_pids "$name")"
  if [ -n "$run" ]; then echo "[$name] 이미 실행 중 (pid $run)"; return; fi
  _need_py
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

# ── 포어그라운드(supervisord) ────────────────────────────────────────────────
_FG_PIDS=()

_fg_cleanup() {                  # 자식 정리 — trap 해제 후라 재진입 없음
  trap - TERM INT EXIT
  if [ "${#_FG_PIDS[@]}" -gt 0 ]; then kill "${_FG_PIDS[@]}" 2>/dev/null || true; fi
  wait 2>/dev/null || true
  rm -f "$PID_DIR/crawler.pid" "$PID_DIR/bot.pid"
}

_fg() {                          # <want_crawler:bool> <want_bot:bool>
  local wc="$1" wb="$2"
  _need_py
  export PYTHONUNBUFFERED=1      # supervisor 가 파이프로 받으므로 블록버퍼링 해제

  local dup=""                   # 백그라운드로 이미 떠 있으면 중복 기동 금지(supervisor 재시작 루프 방지)
  $wc && dup="$dup $(_pids crawler)"
  $wb && dup="$dup $(_pids bot)"
  if [ -n "${dup// /}" ]; then
    echo "이미 실행 중 (pid ${dup# }) → 먼저 'bash sauron.sh stop'" >&2; exit 1
  fi

  # 하나만 띄울 땐 exec: 이 스크립트가 python 으로 대체되어 시그널·종료코드가 그대로 전달된다.
  if ! $wb; then echo "[crawler] 포어그라운드 실행 (exec)"; exec "$PY" "${CRAWLER_ARGS[@]}"; fi
  if ! $wc; then echo "[bot] 포어그라운드 실행 (exec)";     exec "$PY" "${BOT_ARGS[@]}"; fi

  local cpid bpid
  "$PY" "${CRAWLER_ARGS[@]}" & cpid=$!; echo "$cpid" > "$PID_DIR/crawler.pid"
  "$PY" "${BOT_ARGS[@]}"     & bpid=$!; echo "$bpid" > "$PID_DIR/bot.pid"
  _FG_PIDS=("$cpid" "$bpid")
  echo "[crawler] pid $cpid · [bot] pid $bpid — 포어그라운드 대기 (로그: logs/sauron.log · logs/bot.log)"

  trap '_fg_cleanup; exit 143' TERM
  trap '_fg_cleanup; exit 130' INT
  trap '_fg_cleanup' EXIT

  local rc=0
  if [ "${BASH_VERSINFO[0]}" -gt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -ge 3 ]; }; then
    wait -n || rc=$?             # bash 4.3+ : 먼저 죽는 쪽을 잡는다
  else
    while kill -0 "$cpid" 2>/dev/null && kill -0 "$bpid" 2>/dev/null; do sleep 2; done
    if kill -0 "$cpid" 2>/dev/null; then wait "$bpid" || rc=$?; else wait "$cpid" || rc=$?; fi
  fi
  local dead=crawler; kill -0 "$cpid" 2>/dev/null && dead=bot
  # 데몬 자식이 스스로 죽은 건 rc=0 이어도 '비정상'이다. 0으로 빠지면 supervisor 의
  # autorestart=unexpected(기본) 가 정상종료로 보고 재시작을 안 하므로 1로 올린다.
  [ "$rc" -eq 0 ] && rc=1
  echo "[$dead] 종료(rc=$rc) → 나머지 정리 후 스크립트 종료" >&2
  exit "$rc"                     # EXIT trap 이 남은 자식을 정리한다
}

# ── 인자 파싱 ───────────────────────────────────────────────────────────────
cmd=""; with_bot=true; fg=false; only=""
for a in "$@"; do
  case "$a" in
    --nobot)             with_bot=false ;;
    --fg|--foreground)   fg=true ;;
    --crawler)           only=crawler ;;
    --bot)               only=bot ;;
    start|stop|restart|status) [ -z "$cmd" ] && cmd="$a" || { echo "명령 중복: $a" >&2; exit 1; } ;;
    *) echo "사용: bash sauron.sh {start|stop|restart|status} [--nobot] | --fg [--nobot|--bot]" >&2; exit 1 ;;
  esac
done
cmd="${cmd:-start}"

if $fg; then
  [ "$cmd" = start ] || { echo "--fg 는 start 에만 쓸 수 있습니다 (요청: $cmd)" >&2; exit 1; }
  want_crawler=true; want_bot=true
  $with_bot || want_bot=false
  case "$only" in crawler) want_bot=false ;; bot) want_crawler=false ;; esac
  $want_crawler || $want_bot || { echo "띄울 프로세스가 없습니다 (--bot 과 --nobot 동시 사용)" >&2; exit 1; }
  _fg "$want_crawler" "$want_bot"
fi

case "$cmd" in
  start)
    _start crawler "${CRAWLER_ARGS[@]}"
    $with_bot && _start bot "${BOT_ARGS[@]}"
    if $with_bot; then echo "로그: logs/sauron.log (크롤러) · logs/bot.log (봇) — 자정마다 회전"
    else echo "로그: logs/sauron.log — 자정마다 회전"; fi
    ;;
  stop)    _stop crawler; _stop bot ;;
  restart)
    _stop crawler; _stop bot; sleep 1
    _start crawler "${CRAWLER_ARGS[@]}"
    $with_bot && _start bot "${BOT_ARGS[@]}" ;;
  status)  _status crawler; _status bot ;;
esac
