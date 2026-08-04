#!/system/bin/sh
# gpu_prep.sh - LiteRT GPU 로드 직전 메모리 정리 + kgsl(실 GPU 메모리) 리포트.
# 온디바이스 실행용. root 필요 (drop_caches / compact_memory / kgsl debugfs).
#   adb:    adb push gpu_prep.sh /data/local/tmp/ ; adb shell 'su -c "sh /data/local/tmp/gpu_prep.sh"'
#   termux: su -c 'sh /path/to/gpu_prep.sh'
#   system/bin 오버레이로 넣었으면:  su -c gpu_prep.sh

KP=/sys/kernel/debug/kgsl/proc

# 강제종료 대상: OlliteRT + AI Edge Gallery. 설치된 변종은 자동 탐색해 합친다.
PKGS="com.ollitert.llm.server.beta com.google.ai.edge.gallery"
DISC=$(pm list packages 2>/dev/null | sed 's/^package://' | grep -iE 'ollitert|ai\.?edge\.gallery')
PKGS="$PKGS $DISC"

mb() { echo $(( ${1:-0} / 1048576 )); }

# 한 프로세스의 GPU 할당(byte) = /sys/kernel/debug/kgsl/proc/<pid>/mem 의 size 열 합
kgsl_pid_bytes() {
  m="$KP/$1/mem"; [ -f "$m" ] || { echo 0; return; }
  awk 'FNR==1{c=0; for(i=1;i<=NF;i++) if($i=="size") c=i; next}
       c&&($c ~ /^[0-9]+$/){s+=$c} END{print s+0}' "$m" 2>/dev/null
}
kgsl_total_bytes() {
  [ -d "$KP" ] || { echo 0; return; }
  awk 'FNR==1{c=0; for(i=1;i<=NF;i++) if($i=="size") c=i; next}
       c&&($c ~ /^[0-9]+$/){s+=$c} END{print s+0}' "$KP"/*/mem 2>/dev/null
}
pname() {
  n=$(cat /proc/$1/cmdline 2>/dev/null | tr '\0' ' ' | awk '{print $1}')
  [ -z "$n" ] && n=$(cat /proc/$1/comm 2>/dev/null)
  echo "${n:-?}"
}
snap_mem() {
  awk '/^MemFree:/{f=$2}/^MemAvailable:/{a=$2}
       END{printf "  MemFree=%dMB  MemAvailable=%dMB\n", f/1024, a/1024}' /proc/meminfo
  echo "  GPU(kgsl) allocated = $(mb "$(kgsl_total_bytes)") MB"
}
top5_kgsl() {
  [ -d "$KP" ] || { echo "  (kgsl debugfs 없음 - root/debugfs 필요)"; return; }
  for d in "$KP"/*/; do
    p=${d%/}; p=${p##*/}
    case "$p" in ''|*[!0-9]*) continue ;; esac
    b=$(kgsl_pid_bytes "$p")
    [ "${b:-0}" -gt 0 ] 2>/dev/null || continue
    printf '%s %s\n' "$b" "$p"
  done | sort -rn | head -5 | while read -r b p; do
    printf "   %6d MB  pid=%-6s %s\n" "$(mb "$b")" "$p" "$(pname "$p")"
  done
}

echo "========== gpu_prep : GPU 로드 전 정리 =========="
echo "[BEFORE]"; snap_mem
echo "[kgsl top5 - before]"; top5_kgsl

echo "[ACTIONS]"
for pk in $PKGS; do
  [ -n "$pk" ] || continue
  am force-stop "$pk" 2>/dev/null && echo "  force-stop $pk : ok"
done
am kill-all 2>/dev/null && echo "  kill background apps : ok"
sync
echo 3 > /proc/sys/vm/drop_caches 2>/dev/null && echo "  drop_caches : ok"
echo 1 > /proc/sys/vm/compact_memory 2>/dev/null && echo "  compact_memory : ok"
sleep 1

echo "[AFTER]"; snap_mem
echo "[kgsl top5 - after]"; top5_kgsl
echo "================================================="
echo "이제 OlliteRT/Gallery 에서 GPU 모델을 로드해라 (지금이 성공 확률 최고)."
