# -*- coding: utf-8 -*-
"""
summarize/vision.py — 이미지 대체 공지(제목O·본문X·그림O) 구제용 멀티모달 입력 준비.

to_data_url(url): 이미지 URL을 받아 (다운스케일 후) base64 data URL로 변환.
  - Pillow 있으면 LLM_VISION_MAX_PX 이하로 축소(prefill/컨텍스트 절약). 없으면 원본 전송.
  - 실패(네트워크/디코딩)는 None 반환 → 호출부에서 no_content 처리(예외 안 던짐).
"""
import base64

import requests

import config

try:
    from PIL import Image           # noqa: F401 (존재 여부 감지용)
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False                # 없으면 원본 그대로 전송(다운스케일·재인코딩 생략)


def to_data_url(url, max_px=None, timeout=None):
    if not url:
        return None
    max_px = max_px or config.LLM_VISION_MAX_PX
    timeout = timeout or config.REQUEST_TIMEOUT
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        raw = r.content
        mime = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
    except Exception:
        return None
    # 다운스케일(선택): Pillow 있으면 축소 + JPEG 재인코딩
    try:
        import io
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        raw, mime = buf.getvalue(), "image/jpeg"
    except Exception:
        pass  # Pillow 미설치/디코딩 실패 → 원본 그대로 전송(느리지만 동작)
    try:
        b64 = base64.b64encode(raw).decode("ascii")
    except Exception:
        return None
    return f"data:{mime};base64,{b64}"
