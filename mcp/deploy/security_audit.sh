#!/usr/bin/env bash
# mcp/deploy/security_audit.sh — 이 기기의 보안 상태 점검(읽기 전용: 아무것도 바꾸지 않는다).
#
#   sudo bash mcp/deploy/security_audit.sh            # 최근 7일
#   sudo bash mcp/deploy/security_audit.sh 30         # 최근 30일
#   sudo bash mcp/deploy/security_audit.sh 7 | tee ~/audit-$(date +%F).txt
#
# 보는 것: ① 열린 포트 ② SSH 실효 설정(root·암호 로그인 가능 여부) ③ 계정 암호 상태
#          ④ SSH 공격 시도·성공 로그인 ⑤ Caddy(HTTPS) 오류·인증서·접근 로그 ⑥ MCP 접근 로그 ⑦ 방화벽
#          ⑧ 대기 중인 보안 업데이트 ⑨ 커널 네트워크 방어값
set -u
export PATH="$PATH:/usr/sbin:/sbin"
DAYS="${1:-7}"
SINCE="-${DAYS}d"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_LOG_DIR="$(cd "$HERE/.." && pwd)/logs"
DOMAIN="lota7574.asuscomm.com"
TOP=15

if [ "$(id -u)" -ne 0 ]; then
  echo "root 권한 필요: sudo bash $0 ${DAYS}" >&2
  exit 1
fi

B=$'\e[1m'; R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; N=$'\e[0m'
[ -t 1 ] || { B=; R=; G=; Y=; N=; }
h()    { printf '\n%s━━ %s ━━%s\n' "$B" "$1" "$N"; }
ok()   { printf '  %s[OK]%s   %s\n' "$G" "$N" "$1"; }
warn() { printf '  %s[주의]%s %s\n' "$Y" "$N" "$1"; }
bad()  { printf '  %s[위험]%s %s\n' "$R" "$N" "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
# IP 집계: 표준입력의 IPv4/IPv6 주소를 세어 상위 N개
top_ips() { grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}|([0-9a-f]{1,4}:){2,7}[0-9a-f]{1,4}' | sort | uniq -c | sort -rn | head -"$TOP"; }

echo "${B}보안 점검 · $(hostname) · $(date '+%F %T') · 기간 최근 ${DAYS}일${N}"

# ── ① 열린 포트 ────────────────────────────────────────────────
h "① 외부(0.0.0.0/::)에서 받는 포트"
ss -ltnupH | awk '$5 !~ /^(127\.|\[::1\]|172\.16\.42\.1)/' | awk '{printf "  %-4s %-28s %s\n", $1, $5, $7}'
echo "  (공유기 포트포워딩에 걸린 포트만 인터넷에 노출된다 — 공유기 설정과 대조할 것. 기대값: 443→8443 하나)"
ss -ltnH | awk '{print $4}' | grep -qE '^(0\.0\.0\.0|\*|\[::\]):5901$' \
  && warn "VNC(5901)가 모든 주소에서 열림 — 암호화 없음. -localhost=1 + SSH 터널 권장"

# ── ② SSH 실효 설정 ────────────────────────────────────────────
h "② SSH 실효 설정 (sshd -T = 설정파일·기본값 합산 결과)"
if have sshd; then
  SSHD="$(sshd -T 2>/dev/null)"
  [ -z "$SSHD" ] && bad "sshd -T 실패(root 아님 또는 설정 오류) — 아래 판정은 무효"
  v() { awk -v k="$1" '$1==k{print $2; exit}' <<<"$SSHD"; }
  printf '  %-26s %s\n' port "$(v port)" listenaddress "$(v listenaddress)" \
    permitrootlogin "$(v permitrootlogin)" passwordauthentication "$(v passwordauthentication)" \
    kbdinteractiveauthentication "$(v kbdinteractiveauthentication)" pubkeyauthentication "$(v pubkeyauthentication)" \
    permitemptypasswords "$(v permitemptypasswords)" maxauthtries "$(v maxauthtries)" \
    allowusers "$(v allowusers)" allowgroups "$(v allowgroups)"
  case "$(v permitrootlogin)" in
    yes)                               bad  "root SSH 로그인 가능(암호 포함)" ;;
    prohibit-password|without-password) ok  "root는 키로만 SSH 가능(암호 불가)" ;;
    forced-commands-only)              ok   "root는 강제명령 키로만" ;;
    no)                                ok   "root SSH 로그인 불가" ;;
  esac
  if [ -z "$SSHD" ]; then :
  elif [ "$(v passwordauthentication)" = yes ] || [ "$(v kbdinteractiveauthentication)" = yes ]; then
    bad "암호 로그인 가능 — 키 접속 확인 후 PasswordAuthentication no 권장"
  else
    ok "암호 로그인 불가(키만)"
  fi
  [ "$(v permitemptypasswords)" = yes ] && bad "빈 암호 로그인 허용"
else
  warn "sshd 없음"
fi

# ── ③ 계정·암호 상태 ───────────────────────────────────────────
h "③ 계정 (로그인 셸 보유 계정의 암호 상태 · 등록 키)"
while IFS=: read -r user _ uid _ _ home shell; do
  case "$shell" in */nologin|*/false|*/sync|"") continue ;; esac
  st="$(passwd -S "$user" 2>/dev/null | awk '{print $2}')"
  case "$st" in P) s="암호 있음" ;; L) s="잠김(암호 로그인 불가)" ;; NP) s="${R}암호 없음${N}" ;; *) s="$st" ;; esac
  keys=0; [ -f "$home/.ssh/authorized_keys" ] && keys=$(grep -cvE '^\s*(#|$)' "$home/.ssh/authorized_keys")
  printf '  %-12s uid=%-5s %-24s 키 %s개  %s\n' "$user" "$uid" "$s" "$keys" "$shell"
done < /etc/passwd
st_root="$(passwd -S root 2>/dev/null | awk '{print $2}')"
[ "$st_root" = P ] && warn "root에 암호가 설정돼 있음(콘솔/su 가능). SSH 허용 여부는 ②의 permitrootlogin 참조"
echo "  sudo 그룹: $(getent group sudo | cut -d: -f4)"

# ── ④ SSH 공격 시도 · 성공 로그인 ───────────────────────────────
h "④ SSH 인증 로그 (최근 ${DAYS}일)"
SSHLOG="$(journalctl -u ssh -u sshd --since "$SINCE" --no-pager -o short-iso 2>/dev/null)"
n_fail=$(grep -cE 'Failed (password|publickey)|authentication failure' <<<"$SSHLOG")
n_inv=$(grep -cE 'Invalid user' <<<"$SSHLOG")
n_pre=$(grep -cE 'Connection (closed|reset) by .*\[preauth\]|Disconnected from .*\[preauth\]' <<<"$SSHLOG")
echo "  실패 인증 ${n_fail}회 · 없는 계정 시도 ${n_inv}회 · 인증 전 끊김 ${n_pre}회"
if [ $((n_fail + n_inv)) -gt 0 ]; then
  echo "  ${B}실패 상위 IP${N}"
  grep -E 'Failed |Invalid user' <<<"$SSHLOG" | grep -oE 'from [^ ]+' | top_ips | sed 's/^/    /'
  echo "  ${B}시도된 계정명 상위${N}"
  grep -oE 'Invalid user [^ ]+|Failed [a-z]+ for (invalid user )?[^ ]+' <<<"$SSHLOG" | awk '{print $NF}' \
    | sort | uniq -c | sort -rn | head -10 | sed 's/^/    /'
fi
echo "  ${B}성공 로그인${N} (방법·계정·출발지 — 모르는 출발지가 있으면 즉시 확인)"
grep -E 'Accepted ' <<<"$SSHLOG" | awk '{for(i=1;i<=NF;i++){if($i=="Accepted")m=$(i+1);if($i=="for")u=$(i+1);if($i=="from")f=$(i+1)} print m, u, f}' \
  | sort | uniq -c | sort -rn | sed 's/^/    /'
grep -qE 'Accepted password' <<<"$SSHLOG" && warn "암호로 로그인한 기록이 있음"
grep -qE 'Accepted [a-z]+ for root' <<<"$SSHLOG" && warn "root로 SSH 로그인한 기록이 있음"
echo "  ${B}최근 로그인(last)${N}"; last -n 10 -w 2>/dev/null | grep -vE '^(wtmp|$)' | sed 's/^/    /'
if have lastb; then
  echo "  ${B}실패 로그인 상위(lastb)${N}"; lastb -w 2>/dev/null | grep -vE '^(btmp|$)' | awk '{print $3}' | top_ips | sed 's/^/    /'
fi

# ── ⑤ Caddy (HTTPS 입구) ───────────────────────────────────────
h "⑤ Caddy (외부 443 → 8443)"
systemctl is-active --quiet caddy && ok "caddy 실행 중 ($(caddy version 2>/dev/null | awk '{print $1}'))" || bad "caddy 정지"
CADDYLOG="$(journalctl -u caddy --since "$SINCE" --no-pager 2>/dev/null)"
n_tls=$(grep -c 'TLS handshake error' <<<"$CADDYLOG")
echo "  TLS 핸드셰이크 오류 ${n_tls}회 (도메인 없이 IP로 찌르는 스캐너·구형 클라이언트 — 앱까지 못 옴)"
if [ "$n_tls" -gt 0 ]; then
  echo "  ${B}상위 IP${N}"; grep 'TLS handshake error' <<<"$CADDYLOG" | grep -oE 'from [^ ]+' | sed 's/:[0-9]*"*$//' | top_ips | sed 's/^/    /'
  echo "  ${B}오류 종류${N}"; grep 'TLS handshake error' <<<"$CADDYLOG" | grep -oE 'error from [^:]+:[0-9]+: [^"]+' \
    | sed -E 's/error from [^ ]+ //' | sort | uniq -c | sort -rn | head -6 | sed 's/^/    /'
fi
n_acme_err=$(grep -ciE '"level":"error".*(acme|certificate|obtain)' <<<"$CADDYLOG")
[ "$n_acme_err" -gt 0 ] && warn "인증서 발급/갱신 오류 ${n_acme_err}건 — journalctl -u caddy | grep -i acme" || ok "인증서 발급/갱신 오류 없음"
if have openssl; then
  exp=$(echo | timeout 8 openssl s_client -connect 127.0.0.1:8443 -servername "$DOMAIN" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  [ -n "$exp" ] && echo "  인증서 만료: $exp (Caddy가 30일 전부터 자동 갱신)" || warn "8443에서 인증서를 읽지 못함"
fi
ACCESS=/var/log/caddy/mcp-access.log
if grep -qE '^\s*log' /etc/caddy/Caddyfile 2>/dev/null && ls "$ACCESS"* >/dev/null 2>&1 && have jq; then
  ok "Caddy 접근 로그 켜짐 ($ACCESS)"
  # 회전된 파일(mcp-access-<시각>.log, .gz 포함)까지 합쳐 기간 내 요청만
  since_ts=$(date -d "$DAYS days ago" +%s)
  CLOG="$( { cat "${ACCESS%.log}"*.log 2>/dev/null; zcat -f "${ACCESS%.log}"*.gz 2>/dev/null; } \
          | jq -c --argjson t "$since_ts" 'select(.ts >= $t)
                | {ip: .request.remote_ip, m: .request.method, uri: .request.uri, st: .status,
                   host: .request.host, ua: (.request.headers["User-Agent"][0] // "-")}' 2>/dev/null)"
  echo "  요청 $(grep -c . <<<"$CLOG")건 · 고유 IP $(jq -r .ip <<<"$CLOG" | sort -u | grep -c .)개"
  echo "  ${B}상태코드별${N}"; jq -r .st <<<"$CLOG" | sort | uniq -c | sort -rn | sed 's/^/    /'
  echo "  ${B}200이 아닌 요청 상위 IP${N}"; jq -r 'select(.st != 200) | .ip' <<<"$CLOG" | top_ips | sed 's/^/    /'
  echo "  ${B}200이 아닌 요청 상위 경로${N} (/.env·/.git·wp-login 등 = 경로 스캔)"
  jq -r 'select(.st != 200) | "\(.m) \(.uri[0:60])"' <<<"$CLOG" | sort | uniq -c | sort -rn | head -"$TOP" | sed 's/^/    /'
  echo "  ${B}User-Agent 상위${N}"; jq -r '.ua[0:70]' <<<"$CLOG" | sort | uniq -c | sort -rn | head -8 | sed 's/^/    /'
else
  warn "Caddy 접근 로그 꺼짐(또는 아직 요청 없음) — 앱 앞단에서 막힌 요청(경로 스캔 등)은 기록되지 않음"
fi

# ── ⑥ MCP 접근 로그 ────────────────────────────────────────────
h "⑥ MCP 접근 로그 ($MCP_LOG_DIR)"
if ls "$MCP_LOG_DIR"/mcp.log* >/dev/null 2>&1; then
  MCPLOG="$(cat "$MCP_LOG_DIR"/mcp.log* | grep 'sauron-mcp.access' | awk -v s="$(date -d "$DAYS days ago" '+%F')" '$1>=s')"
  echo "  상태코드별:"; awk '{print $(NF-1)}' <<<"$MCPLOG" | sort | uniq -c | sort -rn | sed 's/^/    /'
  echo "  (401=키 없음/틀림 · 429=레이트리밋/키대입 잠금 · 404=/.well-known 등 · 160.79.106.x 대역은 claude.ai 커넥터로 보임)"
  echo "  ${B}200이 아닌 요청 상위 IP${N}"; awk '$(NF-1)!="200"{print $5}' <<<"$MCPLOG" | top_ips | sed 's/^/    /'
  echo "  ${B}200이 아닌 요청 상위 경로${N}"; awk '$(NF-1)!="200"{print $6, $7, $(NF-1)}' <<<"$MCPLOG" | sort | uniq -c | sort -rn | head -10 | sed 's/^/    /'
  grep -q ' 429 ' <<<"$MCPLOG" && warn "429 발생 — 키 대입/과다 요청 IP 있음(위 목록)"
else
  warn "MCP 로그 없음"
fi

# ── ⑦ 방화벽 ──────────────────────────────────────────────────
h "⑦ 방화벽"
if have nft; then
  rules=$(nft list ruleset 2>/dev/null)
  [ -n "$rules" ] && { echo "$rules" | sed 's/^/  /' | head -60; } || warn "nftables 규칙 없음(모두 허용)"
else
  warn "nft 미설치"
fi
if have iptables; then iptables -S 2>/dev/null | sed 's/^/  /'; fi
have ufw && ufw status verbose | sed 's/^/  /'
have fail2ban-client && fail2ban-client status | sed 's/^/  /' || echo "  fail2ban 미설치"

# ── ⑧ 보안 업데이트 ────────────────────────────────────────────
h "⑧ 대기 중인 보안 업데이트 (apt 목록 갱신 없이 마지막 update 기준)"
sec=$(apt list --upgradable 2>/dev/null | grep -- '-security')
if [ -n "$sec" ]; then
  bad "보안 업데이트 $(wc -l <<<"$sec")건 대기:"; sed 's/^/    /' <<<"$sec"
  echo "    → sudo apt update && sudo apt upgrade  (linux-image/grub/firmware-* 가 끌려오면 중단)"
else
  ok "대기 중인 보안 업데이트 없음"
fi
echo "  마지막 apt update: $(stat -c %y /var/lib/apt/lists/partial 2>/dev/null | cut -d. -f1)"

# ── ⑨ 커널 네트워크 방어값 ─────────────────────────────────────
h "⑨ 커널 네트워크 설정"
for k in net.ipv4.tcp_syncookies net.ipv4.conf.all.rp_filter net.ipv4.conf.all.accept_redirects \
         net.ipv4.conf.all.send_redirects net.ipv4.ip_forward net.ipv6.conf.all.accept_ra; do
  printf '  %-38s %s\n' "$k" "$(sysctl -n "$k" 2>/dev/null)"
done
echo "  (권장: syncookies=1 · rp_filter=1또는2 · accept_redirects=0 · send_redirects=0 · ip_forward=1은 USB 관리망(172.16.42.x) 공유용일 수 있으니 확인 전 끄지 말 것)"

echo; echo "${B}끝.${N} 이 스크립트는 아무것도 변경하지 않았다."
