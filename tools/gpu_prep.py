#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gpu_prep.py - PC에서 adb로 gpu_prep.sh 를 폰에 밀어넣고 root로 실행하는 배포기.

실제 로직(메모리 정리 + kgsl 실측 + OlliteRT/Gallery 강제종료 + kgsl top5)은
같은 폴더의 gpu_prep.sh 한 곳에만 있다(단일 소스). 이 파이썬은 그걸 폰으로
push 해서 `su -c sh` 로 돌리고 출력을 그대로 보여줄 뿐이다.

  python gpu_prep.py                 # 같은 폴더의 gpu_prep.sh 사용
  python gpu_prep.py <gpu_prep.sh>   # 다른 경로 지정

폰에서 직접 돌리려면 gpu_prep.py 없이:  su -c 'sh /data/local/tmp/gpu_prep.sh'
"""

import os
import re
import subprocess
import sys

REMOTE = "/data/local/tmp/gpu_prep.sh"
DEFAULT_SH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_prep.sh")


def run(args, timeout=120):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except FileNotFoundError:
        print("adb 를 찾을 수 없다.")
        sys.exit(1)


def pick_serial():
    rc, out = run(["adb", "devices"])
    ts = []
    for line in out.splitlines()[1:]:
        m = re.match(r"^(\S+)\s+device\b", line)
        if m:
            ts.append(m.group(1))
    usb = [t for t in ts if ":" not in t]
    return (usb or ts or [None])[0]


def main():
    sh = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SH
    if not os.path.isfile(sh):
        print(f"gpu_prep.sh 를 못 찾음: {sh}")
        sys.exit(1)

    run(["adb", "start-server"])
    serial = pick_serial()
    if not serial:
        print("연결된 기기 없음.")
        sys.exit(1)
    print(f"[*] 기기: {serial}")
    print(f"[*] push {os.path.basename(sh)} -> {REMOTE}")

    rc, out = run(["adb", "-s", serial, "push", sh, REMOTE])
    if rc != 0:
        print(out)
        sys.exit(1)

    rc, out = run(
        ["adb", "-s", serial, "shell", "su", "-c", f"sh {REMOTE}"], timeout=120
    )
    print(out.rstrip())
    if "GPU 로드" not in out and "kgsl" not in out.lower():
        print("\n[!] 정상 출력이 안 보인다. 폰 화면 켜고 Magisk(su) 허용 후 재시도.")


if __name__ == "__main__":
    main()
