# -*- coding: utf-8 -*-
"""
crawl/apiparse.py — JS 프론트(React/Next) 사이트의 JSON API 응답 파싱 헬퍼.

'데이터로 확장' 설계의 코드측 최소 부분:
  · dig()          — 점 표기 경로로 중첩 dict 진입 (예: "data.content.list").
  · to_html(fmt, raw) — 본문 인코딩(content_format)별로 HTML 문자열로 정규화.
        html    : HTML 엔티티 이스케이프 해제(예: startup boardContent)
        lexical : Lexical 에디터 JSON → HTML(텍스트+<img>) (예: media content)
        plain   : 평문 → <p>
  이후 이미지/요약은 기존 fetcher._extract_images + llm.html_to_text가 그대로 처리.
새 인코딩이 나올 때만 여기 함수 하나 추가하면 된다(그 외 변형은 fetch_config 데이터로 흡수).
"""
import html as _html
import json as _json


def dig(obj, path):
    """'a.b.c' 경로로 중첩 dict/list 진입. 실패 시 None."""
    cur = obj
    for key in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


def _lex_walk(node, out):
    if not isinstance(node, dict):
        return
    t = node.get("type") or ""
    if "image" in t:                                  # Lexical 이미지 노드
        src = node.get("src") or node.get("url")
        if src:
            out.append(f'<img src="{src}" />')
    txt = node.get("text")
    if txt:
        out.append(_html.escape(txt))
    for ch in (node.get("children") or []):
        _lex_walk(ch, out)
    if t in ("paragraph", "heading", "listitem", "list", "quote"):
        out.append("\n")
    elif t == "linebreak":
        out.append("\n")


def lexical_to_html(raw):
    """Lexical 에디터 상태(JSON, 때때로 이중 인코딩된 문자열) → 간단 HTML(텍스트+<img>)."""
    if not raw:
        return ""
    try:
        data = _json.loads(raw)
        if isinstance(data, str):        # 이중 인코딩(문자열 안의 JSON) 방어
            data = _json.loads(data)
    except Exception:
        return raw if isinstance(raw, str) else ""
    if not isinstance(data, dict):
        return ""
    root = (data.get("editorState") or data).get("root", {})
    out = []
    _lex_walk(root, out)
    return "<p>" + "".join(out) + "</p>"


def to_html(fmt, raw):
    """content_format별 본문 → HTML 문자열 정규화."""
    raw = raw or ""
    fmt = (fmt or "html").lower()
    if fmt == "lexical":
        return lexical_to_html(raw)
    if fmt == "html":
        return _html.unescape(raw)       # 엔티티 이스케이프된 HTML(startup 등)
    # plain
    return "<p>" + _html.escape(raw).replace("\n", "<br>") + "</p>"
