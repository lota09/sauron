# -*- coding: utf-8 -*-
"""core/vkpath.py — OCR(MNN Vulkan)용 로더 경로 보장.

MNN 은 런타임에 dlopen("libvulkan.so")(버전 없는 이름)로 Vulkan 을 연다. Debian 엔 개발 패키지 없이는
libvulkan.so.1 만 있어서 .venv/vklib/libvulkan.so 심볼릭 링크를 LD_LIBRARY_PATH 로 보여 줘야 한다.
LD_LIBRARY_PATH 는 프로세스 시작 때만 읽히므로, 빠져 있으면 같은 인자로 자신을 한 번 다시 실행한다(exec).
sauron.sh 로 띄우면 이미 들어 있어 아무 일도 하지 않는다. vklib 이 없거나 OCR 이 꺼져 있어도 아무 일도 안 함.
"""
import os
import sys

_MARK = "SAURON_VKPATH_DONE"


def ensure():
    try:
        import config
        if not config.OCR_ENABLE or str(config.OCR_BACKEND).upper() != "VULKAN":
            return
    except Exception:
        return
    vk = os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "vklib")
    if not os.path.exists(os.path.join(vk, "libvulkan.so")) or os.environ.get(_MARK):
        return
    cur = os.environ.get("LD_LIBRARY_PATH", "")
    if vk in cur.split(":"):
        return
    os.environ["LD_LIBRARY_PATH"] = vk + (":" + cur if cur else "")
    os.environ[_MARK] = "1"                    # 무한 재실행 방지
    sys.stdout.flush(); sys.stderr.flush()
    os.execv(sys.executable, [sys.executable] + sys.argv)
