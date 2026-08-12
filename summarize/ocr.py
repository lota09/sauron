# -*- coding: utf-8 -*-
"""
summarize/ocr.py — OCR (교체가능·온디맨드 백엔드)

get_ocr(name).extract(image_url) -> str
  backends: 'tesseract' | 'paddle' | 'none'
RAM 제약(폰 여유 ~2.5GB) 때문에 무거운 엔진은 온디맨드 로드/해제.
엔진 미설치 환경(개발 샌드박스)에서도 import는 성공해야 하므로 지연 import.
"""
import io
import requests

import config


class OCRBase:
    def extract(self, image_url: str) -> str:
        raise NotImplementedError

    def unload(self):
        pass

    def _download(self, image_url: str):
        from PIL import Image
        headers = {"User-Agent": config.USER_AGENT}
        resp = requests.get(image_url, headers=headers, timeout=config.OCR_TIMEOUT)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        return img.convert("RGB") if img.mode != "RGB" else img


class NullOCR(OCRBase):
    def extract(self, image_url: str) -> str:
        return ""


class TesseractOCR(OCRBase):
    """가벼움. tesseract 바이너리 + kor traineddata 필요. 상주 모델 없음."""

    def extract(self, image_url: str) -> str:
        try:
            import pytesseract
        except ImportError:
            return ""
        try:
            img = self._download(image_url)
            lang = "kor" if config.OCR_LANG.startswith("kor") else config.OCR_LANG
            return (pytesseract.image_to_string(img, lang=lang) or "").strip()
        except Exception:
            return ""


class PaddleOCR_(OCRBase):
    """정확도↑·무거움. 온디맨드 로드 후 unload()로 해제 가능."""

    def __init__(self):
        self._ocr = None

    def _ensure(self):
        if self._ocr is None:
            from paddleocr import PaddleOCR
            self._ocr = PaddleOCR(use_textline_orientation=True, lang="korean")
        return self._ocr

    def extract(self, image_url: str) -> str:
        try:
            import numpy as np
            img = self._download(image_url)
            res = self._ensure().predict(np.array(img))
            if not res:
                return ""
            first = res[0]
            lines = getattr(first, "rec_texts", None)
            if lines is None and isinstance(first, dict):
                lines = first.get("rec_texts")
            return "\n".join(lines) if lines else ""
        except Exception:
            return ""

    def unload(self):
        self._ocr = None


_BACKENDS = {"none": NullOCR, "tesseract": TesseractOCR, "paddle": PaddleOCR_}


def get_ocr(name: str = None) -> OCRBase:
    name = (name or config.OCR_BACKEND or "none").lower()
    return _BACKENDS.get(name, NullOCR)()
