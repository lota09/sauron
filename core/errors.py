# -*- coding: utf-8 -*-
"""core/errors.py — 예외를 사람이 읽을 한 덩어리로.

감시채널·로그에 `str(err)`만 쓰면 KeyError는 키 이름만, 감싼 예외는 겉 메시지만 남아
'어느 모듈에서 왜'를 알 수 없다. 여기서는
  · 감싼 예외(raise X from e / except 안에서 raise)를 끝까지 따라가 **가장 안쪽 원인**을 찾고
  · 그 원인이 **이 저장소의 어느 파일·줄·함수**에서 났는지(라이브러리 내부 프레임은 건너뜀)
를 붙인다. 플러그인도 코어 모듈과 똑같이 처리된다 — 발생 위치가 plugins/xxx.py 로 찍힐 뿐.
"""
import os
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _chain(e):
    """[겉 예외, …, 가장 안쪽 원인]. __cause__(명시적 from) → __context__(except 안에서 raise) 순."""
    out, seen = [], set()
    while e is not None and id(e) not in seen:
        seen.add(id(e)); out.append(e)
        e = e.__cause__ or (None if e.__suppress_context__ else e.__context__)
    return out


def root_cause(e):
    return _chain(e)[-1]


def _in_repo(fr):
    p = os.path.abspath(fr.filename)
    return p.startswith(ROOT + os.sep) and f"{os.sep}.venv{os.sep}" not in p


def _repo_frame(e):
    """'우리 코드의 어디서' 났는지: 가장 안쪽 원인부터 바깥으로 거슬러 가며, 이 저장소 안의
    가장 안쪽 프레임을 찾는다. (네트워크 오류처럼 원인이 socket.py 같은 라이브러리에서 나면
    그걸 부른 우리 코드 위치가 의미 있다.)"""
    for x in reversed(_chain(e)):
        frames = traceback.extract_tb(x.__traceback__) if x.__traceback__ else []
        for fr in reversed(frames):
            if _in_repo(fr):
                return fr
    return None


def where(e):
    """'crawl/fetcher.py:67 _get()' 형태. 위치를 모르면 ''."""
    fr = _repo_frame(e)
    if fr is None:
        return ""
    return f"{os.path.relpath(os.path.abspath(fr.filename), ROOT)}:{fr.lineno} {fr.name}()"


def signature(e):
    """같은 고장인지 판별하는 키: 원인 타입 + 발생 위치(메시지는 숫자 등이 바뀌므로 제외)."""
    return f"{type(root_cause(e)).__name__}@{where(e)}"


def describe(e, limit=300):
    """감시채널용 요약.
         <겉 메시지>
         원인: ReadTimeout: ... read timed out
         @ crawl/fetcher.py:67 _get()  ← resp = self.session.get(...)
    """
    r = root_cause(e)
    lines = [str(e)[:limit]] if r is not e and str(e) else []
    lines.append(f"{'원인: ' if r is not e else ''}{type(r).__name__}: {str(r)[:limit]}")
    fr = _repo_frame(e)
    if fr is not None:
        code = (fr.line or "").strip()
        lines.append(f"@ {where(e)}" + (f"  ← {code[:120]}" if code else ""))
    return "\n".join(lines)


def full(e):
    """로그용 전체 트레이스백(감싼 예외 체인 포함)."""
    return "".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip()
