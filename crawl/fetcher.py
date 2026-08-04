# -*- coding: utf-8 -*-
"""
crawl/fetcher.py — 크롤러 (ICT tools/fetch_tool.py 이식·정리)

핵심 원칙: 크롤 코드는 하나, 사이트 차이는 dept의 CSS 셀렉터 2개 + fetch_type으로 흡수.
  scrape_list(dept, page) -> [{'title','url'}]
  fetch_content(dept, url) -> {'content','images':[{'url','filename'}]}

fetch_type:
  html          : 제네릭 CSS (link_selector / content_selector)
  json_ssfilm   : 영화예술 JSON API
  json_mediamba : 미디어경영 JSON API
  onclick_media : 글로벌미디어 onclick viewData()
  post_lawyer   : 법무 POST 요청
  dom_materials : 신소재 특수 DOM
infocom: 학교서버 버그(Uncaught PDOException) 에러페이지 감지 시 F5처럼 재시도.
"""
import hashlib
import os
import re
import time
from urllib.parse import urljoin, urlparse, unquote

import requests
import urllib3
from bs4 import BeautifulSoup, Comment

import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 학교서버 버그/에러페이지 시그니처 → 재시도 트리거
ERROR_SIGNATURES = ("Uncaught PDOException", "Fatal error", "Integrity constraint violation")


class FetchError(Exception):
    pass


class Fetcher:
    def __init__(self, session: requests.Session = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.8,en-US;q=0.5,en;q=0.3",
            "Connection": "keep-alive",
        })
        self.timeout = config.REQUEST_TIMEOUT

    # ── HTTP with infocom retry ────────────────────────
    def _get(self, url, retry_on_error_page=False):
        tries = config.INFOCOM_RETRY if retry_on_error_page else 1
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
    def _needs_retry(dept) -> bool:
        return "infocom.ssu.ac.kr" in (dept.get("list_url") or "")

    # ── 목록 스크랩 ────────────────────────────────────
    def scrape_list(self, dept, page: int = 1):
        url = (dept["list_url"] or "").replace("{{page}}", str(page))
        ftype = dept.get("fetch_type", "html")
        prefix = dept.get("url_prefix") or ""
        try:
            if ftype == "json_ssfilm":
                return self._list_ssfilm(url)
            if ftype == "json_mediamba":
                return self._list_mediamba(url)
            if ftype == "onclick_media":
                return self._list_media(url, dept.get("link_selector"))
            if ftype == "post_lawyer":
                return self._list_lawyer(url)
            if ftype == "dom_materials":
                return self._list_materials(url)
            return self._list_generic(url, dept.get("link_selector"), prefix,
                                      retry=self._needs_retry(dept))
        except Exception as e:
            raise FetchError(f"scrape_list 실패({dept['dept_id']} p{page}): {e}")

    def _list_generic(self, url, link_selector, prefix, retry=False):
        if not (link_selector and link_selector.strip()):
            return []  # 셀렉터 미정 학과
        resp = self._get(url, retry_on_error_page=retry)
        soup = BeautifulSoup(resp.content, "html.parser")
        out = []
        for a in soup.select(link_selector):
            href = a.get("href")
            text = a.get_text(strip=True)
            if href and text and len(text) > 3:
                full = urljoin(url, href)
                full = full.split("PHPSESSID=")[0]  # 세션id 제거
                if prefix and not full.startswith("http"):
                    full = prefix + href
                out.append({"title": text, "url": full})
        return out

    def _list_ssfilm(self, url):
        data = self._get(url).json()
        out = []
        for item in data.get("data_list", []):
            t = (item.get("Title") or "").strip()
            idx = item.get("NoticeIndex", "")
            if t and idx:
                out.append({"title": t, "url": f"http://ssfilm.ssu.ac.kr/notice/notice_view?NoticeIndex={idx}"})
        return out

    def _list_mediamba(self, url):
        data = self._get(url).json()
        out = []
        if data.get("success"):
            for item in data.get("data", {}).get("boards", [])[:10]:
                t = (item.get("title") or "").strip()
                bid = item.get("id", "")
                if t and bid:
                    out.append({"title": t, "url": f"https://api.mediamba.ssu.ac.kr/v1/board/{bid}"})
        return out

    def _list_media(self, url, link_selector):
        resp = self._get(url)
        soup = BeautifulSoup(resp.content, "html.parser")
        out = []
        for link in soup.select(link_selector or ""):
            oc = link.get("onclick") or ""
            m = re.search(r"viewData\('(\d+)'\)", oc)
            text = link.get_text(strip=True)
            if m and text and len(text) > 3:
                out.append({"title": text,
                            "url": f"http://media.ssu.ac.kr/sub.php?code=XxH00AXY&mode=view&board_num={m.group(1)}&category=1"})
        return out

    def _list_lawyer(self, url):
        resp = self._get(url)
        soup = BeautifulSoup(resp.content, "html.parser")
        out = []
        for item in soup.select("#main > section.contents > div.board-list-style.board-course > div.board-list-body > div"):
            _id = item.get("id")
            if not _id:
                continue
            te = item.select_one("p.b-title > a")
            if te:
                t = te.get_text(strip=True)
                if t and len(t) > 3:
                    out.append({"title": t, "url": f"https://lawyer.ssu.ac.kr/web/05/notice_view.do?post={_id}"})
        return out

    def _list_materials(self, url):
        resp = self._get(url)
        soup = BeautifulSoup(resp.content, "html.parser")
        out = []
        for item in soup.select(".news-list ul li"):
            a = item.select_one("a")
            te = item.select_one(".tit_box strong")
            if a and a.get("href") and te:
                for span in te.select("span"):
                    span.decompose()
                t = te.get_text(strip=True)
                if t and len(t) > 3:
                    out.append({"title": t, "url": urljoin(url, a.get("href"))})
        return out

    # ── 상세 본문 ──────────────────────────────────────
    def fetch_content(self, dept, url):
        ftype = dept.get("fetch_type", "html")
        try:
            if ftype == "json_ssfilm":
                content = self._content_ssfilm(url)
            elif ftype == "json_mediamba":
                content = self._content_mediamba(url)
            elif ftype == "post_lawyer":
                content = self._content_lawyer(url, dept.get("content_selector"))
            else:
                content = self._content_generic(url, dept.get("content_selector"),
                                                 retry=self._needs_retry(dept))
        except Exception as e:
            raise FetchError(f"fetch_content 실패({url}): {e}")

        images = self._extract_images(content, url) if content else []
        return {"content": self._clean_html(content), "images": images}

    def _content_generic(self, url, content_selector, retry=False):
        resp = self._get(url, retry_on_error_page=retry)
        soup = BeautifulSoup(resp.content, "html.parser")
        if content_selector and content_selector.strip():
            el = soup.select_one(content_selector)
            return str(el) if el else ""
        return ""

    def _content_ssfilm(self, url):
        try:
            return (self._get(url).json().get("data_modify", {}).get("Content", "") or "").strip()
        except Exception:
            return ""

    def _content_mediamba(self, url):
        data = self._get(url).json()
        return (data.get("data", {}).get("content", "") or "").strip() if data else ""

    def _content_lawyer(self, url, content_selector):
        base = url.split("?post=")[0]
        post_id = url.split("?post=")[-1]
        resp = self.session.post(base, data={"pdsid": post_id}, timeout=self.timeout, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")
        if content_selector:
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
