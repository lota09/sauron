# -*- coding: utf-8 -*-
"""
crawl/fetcher.py — 크롤러 (ICT tools/fetch_tool.py 이식·정리)

핵심 원칙: 크롤 코드는 하나, 사이트 차이는 dept의 CSS 셀렉터 2개 + fetch_type으로 흡수.
  scrape_list(dept, page) -> [{'title','url'}]
  fetch_content(dept, url) -> {'content','images':[{'url','filename'}]}

fetch_type (사이트 이름이 아니라 '수집 방식'만 존재한다 — 사이트 차이는 전부 depts 행의 설정):
  html      : link_selector / content_selector 로 목록·본문을 고른다.
              fetch_config(선택)로 범용 옵션을 켠다. 없으면 기본 동작.
                링크 조립   {"link_attr": "data-params", "url_template": "view.do?seq={seq}"}  ← 속성값이 JSON
                            {"link_attr": "onclick", "link_regex": "fnView\\('(?P<id>\\d+)'\\)",
                             "url_template": "view.do?id={id}"}                              ← 속성값에서 정규식 추출
                            (href에서 쿼리를 떼는 데도 쓴다: link_attr=href, link_regex="^(?P<p>[^?#]+)")
                제목 영역   {"title_selector": ".tit_box strong", "title_exclude": "span"}
                            링크가 카드 전체를 감쌀 때, 링크 안에서 제목만 고르고 뱃지 등을 뺀다.
                에러페이지  {"error_page_retry": 3}
                            서버가 200과 함께 PHP 에러페이지를 줄 때 새로고침처럼 재시도(ERROR_SIGNATURES).
  json_api  : 화면을 JS로 그리는 사이트의 JSON API. fetch_config 필수:
              list_url({page})·list_path·id_key·title_key·url_template·content_key·content_format
              ·(선택)page_base·headers·detail_path
              detail_path가 있으면 본문을 목록이 아니라 '공지 URL이 돌려주는 JSON'의 그 경로에서 읽는다.
  그 외     : 플러그인 — plugins/<fetch_type>.py 의 SourcePlugin(로그인이 필요한 사이트 등).
              fetch_config는 플러그인의 self.config 로 전달된다. 틀은 core/plugin.py.
플러그인도 없는 fetch_type은 조용히 html로 떨어지지 않고 실패한다(설정 오류를 숨기지 않음).
"""
import hashlib
import json
import os
import re
import time
from urllib.parse import urljoin, urlparse, unquote

import requests
import urllib3
from bs4 import BeautifulSoup, Comment

import config
from core import plugin
from crawl import apiparse

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 학교서버 버그/에러페이지 시그니처 → 재시도 트리거
ERROR_SIGNATURES = ("Uncaught PDOException", "Fatal error", "Integrity constraint violation")


class FetchError(Exception):
    pass


BUILTIN_TYPES = ("html", "json_api")


def _new_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.8,en-US;q=0.5,en;q=0.3",
        "Connection": "keep-alive",
    })
    return s


class Fetcher:
    def __init__(self, session: requests.Session = None):
        self.session = session or _new_session()
        self.timeout = (config.REQUEST_CONNECT_TIMEOUT, config.REQUEST_TIMEOUT)  # (connect, read): 죽은 호스트 5초 실패
        self._json_cache = {}   # json_api: dept_id → {notice_url: item} (scrape_list이 채움)
        self._plugins = {}      # dept_id → SourcePlugin 인스턴스(프로세스 동안 유지 → 로그인 세션 재사용)
        self._plugin_content = {}   # 플러그인이 목록에서 준 본문: url → html(있으면 상세 요청 생략)

    # ── 플러그인 ────────────────────────────────────────
    def _plugin(self, dept):
        """학과의 수집 플러그인. 학과마다 인스턴스 하나, 전용 세션(쿠키가 다른 사이트와 섞이지 않게)."""
        ftype, did = dept.get("fetch_type"), dept["dept_id"]
        p = self._plugins.get(did)
        if p is None or p.name != ftype:
            cfg = dept.get("fetch_config") or {}
            cfg = cfg if isinstance(cfg, dict) else json.loads(cfg)
            p = plugin.create(ftype, "source", config=cfg, session=_new_session(), timeout=self.timeout)
            self._plugins[did] = p
        return p

    def session_for(self, dept):
        """이 학과의 파일(이미지 등)을 받을 세션. 플러그인 학과는 그 플러그인의 로그인 세션,
        그 외는 None(=로그인 없이). 로그인이 필요한 사이트의 첨부는 로그인 없이 받으면 HTML이 온다."""
        if dept.get("fetch_type", "html") in BUILTIN_TYPES:
            return None
        try:
            return self._plugin(dept).session
        except Exception:
            return None

    def paginated(self, dept):
        """list_url의 {{page}} 처럼 2·3페이지를 긁을 수 있는가(시딩·재크롤 페이지 수 결정)."""
        ftype = dept.get("fetch_type", "html")
        if ftype == "html":
            return "{{page}}" in (dept.get("list_url") or "")
        if ftype == "json_api":
            return False
        return bool(plugin.load_class(ftype, "source").PAGINATED)

    # ── HTTP (+ 에러페이지 재시도) ─────────────────────
    def _get(self, url, retry_on_error_page=0):
        """retry_on_error_page: 에러페이지(ERROR_SIGNATURES)일 때 재시도할 횟수. 0이면 1회만."""
        tries = max(1, int(retry_on_error_page or 0))
        last = None
        for i in range(tries):
            resp = self.session.get(url, timeout=self.timeout, verify=False)
            resp.raise_for_status()
            text = resp.text
            if retry_on_error_page and any(sig in text[:4000] for sig in ERROR_SIGNATURES):
                last = resp
                time.sleep(0.6)
                continue
            return resp
        return last  # 마지막(에러페이지일 수 있음) 반환

    @staticmethod
    def _html_cfg(dept):
        """html 학과의 선택 옵션(fetch_config). 없으면 {} = 기본 동작."""
        raw = dept.get("fetch_config")
        if not raw:
            return {}
        return raw if isinstance(raw, dict) else json.loads(raw)

    def _retries(self, dept):
        return int(self._html_cfg(dept).get("error_page_retry") or 0)

    # ── 목록 스크랩 ────────────────────────────────────
    def scrape_list(self, dept, page: int = 1):
        url = (dept["list_url"] or "").replace("{{page}}", str(page))
        ftype = dept.get("fetch_type", "html")
        prefix = dept.get("url_prefix") or ""
        try:
            if ftype == "json_api":
                return self._list_json_api(dept)
            if ftype == "html":
                return self._list_generic(url, dept.get("link_selector"), prefix,
                                          retry=self._retries(dept), cfg=self._html_cfg(dept))
            return self._list_plugin(dept, page)
        except Exception as e:
            raise FetchError(f"scrape_list 실패({dept['dept_id']} p{page}): {e}")

    def _list_plugin(self, dept, page):
        """플러그인 목록 → 코어 형식 확인. 형식이 틀리면 조용히 넘기지 않고 실패."""
        out = []
        for i, it in enumerate(self._plugin(dept).list(page) or []):
            if not (isinstance(it, dict) and it.get("title") and it.get("url")):
                raise FetchError(f"plugins/{dept['fetch_type']}.py list() {i}번째 항목에 title·url 필요: {str(it)[:120]}")
            if it.get("content"):
                self._plugin_content[it["url"]] = it["content"]
            out.append({"title": str(it["title"]).strip(), "url": it["url"]})
        return out

    @staticmethod
    def _build_link(el, cfg, base_url):
        """요소의 속성값에서 키를 뽑아 url_template을 채운다. 키가 없는 요소(썸네일 링크 등)는 None.
        link_regex가 있으면 정규식(이름 있는 그룹 → {name}, 번호 그룹 → {0}{1}…), 없으면 JSON으로 해석."""
        raw = el.get(cfg.get("link_attr", "href"))
        if not raw:
            return None
        if cfg.get("link_regex"):
            m = re.search(cfg["link_regex"], raw)
            if not m:
                return None
            args, kwargs = m.groups(), m.groupdict()
        else:
            kwargs = json.loads(raw)
            args = ()
        try:
            return urljoin(base_url, cfg["url_template"].format(*args, **kwargs))
        except (KeyError, IndexError) as e:
            # 템플릿 키가 속성에 없음 = 설정 오류(사이트 구조 변경 포함). 조용히 넘기면 전건 누락이라 터뜨린다.
            raise FetchError(f"url_template 키 불일치: {e} (속성값={raw[:120]})")

    @staticmethod
    def _title_of(el, cfg):
        """제목 텍스트. title_selector가 있으면 링크 안의 그 영역만, title_exclude는 읽기 전에 뺀다(뱃지 등)."""
        sel = cfg.get("title_selector")
        te = el.select_one(sel) if sel else el
        if te is None:
            return ""
        if cfg.get("title_exclude"):
            te = BeautifulSoup(str(te), "html.parser")      # 원본 트리를 건드리지 않게 복사본에서 제거
            for x in te.select(cfg["title_exclude"]):
                x.decompose()
        return te.get_text(strip=True)

    def _list_generic(self, url, link_selector, prefix, retry=0, cfg=None):
        if not (link_selector and link_selector.strip()):
            return []  # 셀렉터 미정 학과
        cfg = cfg or {}
        link_cfg = cfg if cfg.get("url_template") else None
        resp = self._get(url, retry_on_error_page=retry)
        soup = BeautifulSoup(resp.content, "html.parser")
        out = []
        for a in soup.select(link_selector):
            text = self._title_of(a, cfg)
            if link_cfg:
                full = self._build_link(a, link_cfg, url)
                if full and text and len(text) > 3:
                    out.append({"title": text, "url": full})
                continue
            href = a.get("href")
            if href and text and len(text) > 3:
                full = urljoin(url, href)
                full = full.split("PHPSESSID=")[0]  # 세션id 제거
                if prefix and not full.startswith("http"):
                    full = prefix + href
                out.append({"title": text, "url": full})
        return out

    # ── json_api: 설정(fetch_config)로 구동되는 제네릭 JSON API 크롤 ──
    #   새 API 사이트 = depts 행 + fetch_config JSON만 추가(코드 X). 본문 인코딩만 apiparse에서 처리.
    def _fetch_cfg(self, dept):
        raw = dept.get("fetch_config")
        if not raw:
            raise FetchError(f"{dept.get('dept_id')}: fetch_config 없음(json_api 필수)")
        return raw if isinstance(raw, dict) else json.loads(raw)

    def _get_json(self, url, headers=None):
        r = self.session.get(url, headers=(headers or {}), timeout=self.timeout, verify=False)
        r.raise_for_status()
        return r.json()

    def _list_json_api(self, dept, page=None):
        """fetch_config: list_url({page})·list_path·id_key·title_key·url_template·content_key·content_format
        ·(선택)page_base(기본1)·headers. 본문이 목록 응답에 인라인이라 상세 재요청 불필요 → item을 캐시.
        page=None이면 page_base(정상 크롤). 특정 페이지 조회 시 page 지정(캐시 미스 재처리용)."""
        cfg = self._fetch_cfg(dept)
        p = cfg.get("page_base", 1) if page is None else page
        url = cfg["list_url"].format(page=p)
        data = self._get_json(url, cfg.get("headers"))
        arr = apiparse.dig(data, cfg["list_path"]) or []
        idk, tk, tmpl = cfg["id_key"], cfg["title_key"], cfg["url_template"]
        cache = self._json_cache.setdefault(dept["dept_id"], {})
        cache.clear()
        out = []
        for it in arr:
            if not isinstance(it, dict):
                continue
            cid = it.get(idk)
            title = (it.get(tk) or "").strip()
            if cid is None or not title:
                continue
            nurl = tmpl.format(id=cid)
            cache[nurl] = it
            out.append({"title": title, "url": nurl})
        return out

    def _content_json_api(self, dept, url):
        cfg = self._fetch_cfg(dept)
        if cfg.get("detail_path"):          # 목록엔 본문이 없고, 공지 URL 자체가 상세 JSON을 준다
            data = self._get_json(url, cfg.get("headers"))
            raw = apiparse.dig(data, cfg["detail_path"]) or ""
            html_content = apiparse.to_html(cfg.get("content_format", "html"), raw)
            images = self._extract_images(html_content, url) if html_content else []
            return {"content": self._clean_html(html_content), "images": images}
        it = self._json_cache.get(dept["dept_id"], {}).get(url)
        if it is None:                       # 캐시 미스(예: query 재처리·깊은 페이지) → 페이지를 훑어 재조회
            base = cfg.get("page_base", 1)
            for p in range(base, base + config.JSON_API_SCAN_PAGES):
                self._list_json_api(dept, p)
                it = self._json_cache.get(dept["dept_id"], {}).get(url)
                if it is not None:
                    break
        if it is None:
            return {"content": "", "images": []}
        raw = it.get(cfg.get("content_key") or "") or ""
        html_content = apiparse.to_html(cfg.get("content_format", "html"), raw)
        images = self._extract_images(html_content, url) if html_content else []
        return {"content": self._clean_html(html_content), "images": images}

    # ── 상세 본문 ──────────────────────────────────────
    def fetch_content(self, dept, url):
        ftype = dept.get("fetch_type", "html")
        if ftype == "json_api":
            try:
                return self._content_json_api(dept, url)
            except Exception as e:
                raise FetchError(f"fetch_content json_api 실패({url}): {e}")
        try:
            if ftype == "html":
                content = self._content_generic(url, dept.get("content_selector"), retry=self._retries(dept))
            else:
                content = self._plugin_content.pop(url, None)
                if content is None:
                    d = self._plugin(dept).detail(url)
                    if not (isinstance(d, dict) and "content" in d):
                        raise FetchError(f"plugins/{ftype}.py detail()은 {{'content': html}}을 돌려줘야 함: {str(d)[:120]}")
                    content = d["content"] or ""
        except Exception as e:
            raise FetchError(f"fetch_content 실패({url}): {e}")

        images = self._extract_images(content, url) if content else []
        return {"content": self._clean_html(content), "images": images}

    def _content_generic(self, url, content_selector, retry=0):
        resp = self._get(url, retry_on_error_page=retry)
        soup = BeautifulSoup(resp.content, "html.parser")
        if content_selector and content_selector.strip():
            el = soup.select_one(content_selector)
            return str(el) if el else ""
        return ""

    # ── HTML 정제 / 이미지 추출 (ICT 이식) ─────────────
    @staticmethod
    def _clean_html(html_content):
        if not html_content or not html_content.strip():
            return html_content or ""
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
                c.extract()
            for tag in soup(["script", "style"]):
                tag.decompose()
            for tag in soup.find_all():
                tag.attrs = {}
            return re.sub(r"\n\s*\n\s*\n", "\n\n", str(soup)).strip()
        except Exception:
            return html_content

    @staticmethod
    def _img_base(u):
        # WordPress 리사이즈 변형(-1568x2216 등)만 제거해 '같은 원본의 여러 해상도'를 묶는다.
        # ⚠ '_1','_2','_3'(밑줄+숫자)은 '붙임 1·2·3'처럼 서로 다른 파일이므로 제거 금지.
        #    (예전엔 여기서 _숫자까지 지워 3장짜리 공지가 1장으로 뭉개졌다 — 회귀 방지: test_image_multi_extract)
        return re.sub(r"-\d+x\d+(?=\.[^.]*$)", "", u)

    @staticmethod
    def _img_dims(u):
        m = re.search(r"-(\d+)x(\d+)(?=\.[^.]*$)", u)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    def _extract_images(self, content, base_url):
        soup = BeautifulSoup(content or "", "html.parser")
        urls = set()
        for img in soup.find_all("img", src=True):
            u = img["src"]
            if base_url and not u.startswith("http"):
                u = urljoin(base_url, u)
            urls.add(u)
        for img in soup.find_all("img", srcset=True):
            for part in img["srcset"].split(","):
                u = part.strip().split(" ")[0]
                if base_url and not u.startswith("http"):
                    u = urljoin(base_url, u)
                if u:
                    urls.add(u)
        groups = {}
        for u in urls:
            groups.setdefault(self._img_base(u), []).append(u)
        out = []
        for _, us in groups.items():
            largest = max(us, key=lambda u: self._img_dims(u)[0] * self._img_dims(u)[1])
            fn = unquote(os.path.basename(urlparse(largest).path))
            if not fn or "." not in fn:
                fn = f"image_{hashlib.md5(largest.encode()).hexdigest()[:8]}.jpg"
            fn = re.sub(r"[^\w\-_.]", "_", fn)
            out.append({"url": largest, "filename": fn})
        return out
