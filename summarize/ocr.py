# -*- coding: utf-8 -*-
"""
summarize/ocr.py — 공지 이미지 OCR (RapidOCR + MNN, 기본 GPU Vulkan). 크롤러 프로세스 안에서 돈다.

설계(docs/local_llm_ocr_plan.md §3.2·§4):
  · 엔진 1개를 지연 생성해 들고 있는다(모델 로드 ≈0.6s, RSS ≈0.4GB). 유휴가 길면 release()로 반납.
  · 동시 1건(_LOCK). 워커가 OCR 워커 1개뿐이지만, 시간초과로 버려진 호출이 아직 GPU를 쓰는 중일 수 있어 잠근다.
  · GPU를 쓰는 목적은 속도가 아니라 'OCR 중에도 큰 코어(4-7)를 LLM에 비워 두기'. 그래서 전·후처리를 하는
    호출 스레드도 작은 코어(OCR_CPUS, 기본 0-3)에 묶는다(sched_setaffinity(0)는 호출 스레드 단위).
  · Vulkan 은 MNN 이 런타임에 dlopen("libvulkan.so")(버전 없는 이름)로 연다. Debian 은 개발 패키지 없이는
    libvulkan.so.1 만 있으므로 sauron.sh 가 .venv/vklib/libvulkan.so 심볼릭 링크를 LD_LIBRARY_PATH 에 넣는다.
    못 찾으면 MNN 은 조용히 CPU로 내려간다 → backend_info()로 확인하고 로그에 남긴다.

ocr_one(data_url) -> (텍스트, 신뢰도, 글자수) : 장 단위. 공지 단위 판정(글자수·신뢰도)은 worker._run_ocr.
"""
import base64
import logging
import os
import threading
import time
import types

import config

log = logging.getLogger("sauron.ocr")

_LOCK = threading.Lock()
_ENGINE = None
_BACKEND_USED = None     # MNN getSessionInfo(…, 2) 값: 0=CPU, 3=OpenCL, 7=Vulkan
_LAST_USE = 0.0
BACKEND_NAMES = {0: "CPU", 3: "OPENCL", 7: "VULKAN"}


class OCRUnavailable(Exception):
    """의존성(rapidocr/MNN) 없음 또는 엔진 생성 실패."""


def _cpus():
    out = set()
    for part in str(config.OCR_CPUS).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def _pin_thread():
    try:
        cpus = _cpus() & os.sched_getaffinity(0) or _cpus()
        os.sched_setaffinity(0, cpus)
    except Exception as e:       # 고정 실패는 치명적이지 않다(속도 영향만)
        log.debug("OCR 스레드 코어 고정 실패: %s", e)


def _build_engine():
    """RapidOCR 엔진 생성. MNN 세션 백엔드를 config.OCR_BACKEND 로 강제한다(RapidOCR는 CPU 고정이라 패치)."""
    global _BACKEND_USED
    try:
        import MNN
        from rapidocr.inference_engine.mnn import main as mm
        from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType, EngineType
    except Exception as e:
        raise OCRUnavailable(f"OCR 패키지 없음: {type(e).__name__}: {e}") from e

    backend = str(config.OCR_BACKEND).upper()

    class Interp:
        def __init__(self, path):
            self._i = MNN.Interpreter(path)

        def createSession(self, *a, **k):
            global _BACKEND_USED
            s = self._i.createSession({"backend": backend, "precision": "normal",
                                       "numThread": config.OCR_THREADS})
            try:
                _BACKEND_USED = int(self._i.getSessionInfo(s, 2))
            except Exception:
                pass
            return s

        def __getattr__(self, n):
            return getattr(self._i, n)

    ns = types.SimpleNamespace(**{k: getattr(MNN, k) for k in dir(MNN) if not k.startswith("__")})
    ns.Interpreter = Interp
    mm.MNN = ns
    t0 = time.time()
    eng = RapidOCR(params={
        "Det.engine_type": EngineType.MNN, "Rec.engine_type": EngineType.MNN,
        "Cls.engine_type": EngineType.MNN,
        "Rec.lang_type": LangRec.KOREAN, "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE, "Global.use_cls": False,
        "Global.log_level": "warning",          # 생성자가 로거 레벨을 이 값으로 덮어쓴다(기본 info = 컬러 로그 여러 줄)
    })
    log.info("[OCR] 엔진 로드 %.1fs · 요청 백엔드 %s", time.time() - t0, backend)
    return eng


_CHECKED = False


def _check_backend():
    """MNN 세션은 첫 추론 때 만들어진다 → 실제 백엔드는 첫 호출 뒤에야 알 수 있다(1회만 확인)."""
    global _CHECKED
    if _CHECKED:
        return
    _CHECKED = True
    want = str(config.OCR_BACKEND).upper()
    got = backend_used()
    if want != "CPU" and got != want:
        log.warning("[OCR] %s 백엔드를 못 열어 %s 로 동작 — LD_LIBRARY_PATH(.venv/vklib/libvulkan.so) 확인. "
                    "작은 코어(OCR_CPUS)에서 CPU로 돌아 느리다", want, got)
    else:
        log.info("[OCR] 실제 백엔드 %s", got)


def backend_used():
    return BACKEND_NAMES.get(_BACKEND_USED, None if _BACKEND_USED is None else str(_BACKEND_USED))


def _lines(result):
    """박스를 세로 위치로 묶어 줄을 재구성(같은 줄은 왼쪽→오른쪽, 칸 구분 ' | '). 표 공지의 행이 보존된다.
    신뢰도 OCR_BOX_MIN_SCORE 미만 박스는 버린다(디자인 글꼴·장식에서 나온 깨진 글자)."""
    if result is None or result.boxes is None or not result.txts:
        return ""
    scores = list(result.scores or [1.0] * len(result.txts))
    keep = [(b, t) for b, t, sc in zip(result.boxes.tolist(), result.txts, scores)
            if sc >= config.OCR_BOX_MIN_SCORE]
    items = sorted(keep, key=lambda b: sum(p[1] for p in b[0]) / 4)
    lines, cur, cy = [], [], None
    for box, txt in items:
        y = sum(p[1] for p in box) / 4
        h = abs(box[2][1] - box[0][1])
        if cy is None or abs(y - cy) < max(8, h * 0.5):
            cur.append((box[0][0], txt))
            cy = y if cy is None else cy
        else:
            lines.append(cur)
            cur, cy = [(box[0][0], txt)], y
    if cur:
        lines.append(cur)
    return "\n".join(" | ".join(t for _, t in sorted(ln)) for ln in lines)


def _decode(data_url):
    import cv2
    import numpy as np
    raw = base64.b64decode(data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url)
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("이미지 디코딩 실패")
    return img


def _confidence(result):
    """글자수 가중 평균 신뢰도(0~1)와 글자수. 박스가 없으면 (0.0, 0).
    실측: 정상 판독 공지 0.88~0.98, 디자인 포스터(깨진 글자) 0.71."""
    if result is None or not result.txts:
        return 0.0, 0
    scores = list(result.scores or [1.0] * len(result.txts))
    w = [len(t) for t in result.txts]
    n = sum(w)
    return (sum(sc * k for sc, k in zip(scores, w)) / n if n else 0.0), n


def ocr_one(data_url):
    """이미지 1장 OCR → (줄 재구성 텍스트, 신뢰도, 원 글자수). 동기 호출(asyncio.to_thread 로 부른다)."""
    global _ENGINE, _LAST_USE
    _pin_thread()
    with _LOCK:
        if _ENGINE is None:
            _ENGINE = _build_engine()
        _LAST_USE = time.time()
        res = _ENGINE(_decode(data_url))
        _LAST_USE = time.time()
        _check_backend()
    conf, n = _confidence(res)
    return _lines(res), conf, n


def release(idle_sec=None):
    """엔진 반납(≈0.4GB 회수). idle_sec 를 주면 그만큼 안 쓴 경우에만. 반납했으면 True."""
    global _ENGINE
    if _ENGINE is None:
        return False
    if idle_sec is not None and time.time() - _LAST_USE < idle_sec:
        return False
    if not _LOCK.acquire(blocking=False):      # 사용 중이면 다음 기회에
        return False
    try:
        _ENGINE = None
    finally:
        _LOCK.release()
    import gc
    gc.collect()
    log.info("[OCR] 유휴 %ds → 엔진 반납", int(idle_sec or 0))
    return True
