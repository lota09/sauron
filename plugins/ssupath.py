# -*- coding: utf-8 -*-
"""plugins/ssupath.py — 슈패스(path.ssu.ac.kr) 비교과 프로그램 수집 (로그인 필요).

CSV 예:  fetch_type=ssupath · fetch_config={"exclude_org": ["진로취업팀"]}
비밀:    secrets/plugin_ssupath.json  {"userid": "학번", "pwd": "비밀번호"}

로그인 절차 (2026-09 실측, experiments/ssupath_probe.py · docs/ssu_path_login_investigation.md):
  1. 목록을 요청하면 로그인 페이지로 이동 → 주소의 rtnUrl을 받는다
  2. loginChk.do?rtnUrl=… → 303 → smartid.ssu.ac.kr 통합로그인 폼(LoginInfo)
  3. smln_pcs.asp 로 userid·pwd 를 **평문 POST**(클라이언트 암호화 없음)
  4. 응답 HTML의 `parent.location.href = '…loginProc.do?…'` 로 이동 — **Referer 필수**
     (없으면 error_referer.jsp 로 튕김). 이 이동이 슈패스 세션(JSESSIONID)을 만든다.
목록·상세:
  · 목록은 서버가 만든 HTML. 제목 링크 a.tit.detailBtn 의 data-params 에 encSddpbSeq(32자 hex).
    이 키는 서로 다른 로그인 세션에서도 같다(실측) → 상세 URL을 공지의 정체성으로 쓴다.
  · 검색 조건을 서버가 세션에 기억한다 → 필터 파라미터를 빈 값까지 전부 명시해야 한다
    (빠뜨리면 직전 검색어가 새어 들어옴).
  · sort=0001 = 최신 등록순. 페이지당 10건.
  · 운영연도(operYySh)는 달력 연도가 아니라 **학년도**라 서버의 기본값(=올해)만 보면 놓친다(실측):
      - 2025학년도 겨울학기 프로그램은 2026년 1월에 신청(운영연도 2025)
      - 내년도 프로그램이 미리 등록됨(캐나다 MITACS: 2026년 8월 신청, 운영연도 2027)
    빈 값은 '올해'로 처리된다(전체 연도 아님). 그래서 작년·올해·내년을 모두 조회한다.
  · 상세 본문은 #tilesContent (상세 정보 표 + 강좌정보 표 — 교육 일시·장소 포함).
계정 잠금 방지:
  · 세션이 끊겨 로그인 페이지로 튕기면 재로그인은 한 번만.
  · 자격증명이 거부되면 이 프로세스가 끝날 때까지 로그인을 다시 시도하지 않는다
    (크롤은 10분마다 돌기 때문 — 틀린 비밀번호로 계속 두드리면 계정이 잠길 수 있다).
    비밀 파일을 고친 뒤 크롤러를 재시작하면 풀린다.
"""
import json
import re
import time
from datetime import datetime
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from core.plugin import SourcePlugin

PATH = "https://path.ssu.ac.kr"
SMARTID = "https://smartid.ssu.ac.kr"
LIST = f"{PATH}/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do"
INFO = f"{PATH}/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmInfo.do"
LOGINCHK = f"{PATH}/comm/login/user/loginChk.do"
DELAY = 1.0     # 요청 간격(초) — 학교 서버에 부담 주지 않게


class SsuPath(SourcePlugin):
    SECRETS = ("userid", "pwd")

    def __init__(self, **kw):
        super().__init__(**kw)
        self._logged_in = False
        self._blocked = None        # 자격증명 거부 사유. 채워지면 재시작 전까지 로그인 시도 안 함

    # ── HTTP ─────────────────────────────────────────
    def _get(self, url, **kw):
        time.sleep(DELAY)
        r = self.session.get(url, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    @staticmethod
    def _is_login(r):
        return "/comm/login/" in r.url or "smartid.ssu.ac.kr" in r.url

    # ── 로그인 ───────────────────────────────────────
    def _login(self):
        if self._blocked:
            raise PermissionError(self._blocked)
        r = self._get(LIST)
        if not self._is_login(r):                       # SSO 쿠키가 살아 있어 바로 통과
            self._logged_in = True
            return
        m = re.search(r"rtnUrl=([^&]+)", r.url)
        if not m:
            raise RuntimeError(f"로그인 페이지 주소에 rtnUrl이 없음 — 사이트 구조 변경? ({r.url[:120]})")
        r2 = self._get(LOGINCHK, params={"rtnUrl": unquote(m.group(1))})
        if not self._is_login(r2):
            self._logged_in = True
            return
        form = BeautifulSoup(r2.text, "html.parser").find("form", attrs={"name": "LoginInfo"})
        if form is None:
            raise RuntimeError("smartid 로그인 폼(LoginInfo)이 없음 — 사이트 구조 변경?")
        data = {i.get("name"): (i.get("value") or "") for i in form.find_all("input")
                if i.get("name") and i.get("name") != "chkSave"}
        data.update({"userid": self.secrets["userid"], "pwd": self.secrets["pwd"]})
        time.sleep(DELAY)
        r3 = self.session.post(urljoin(r2.url, form.get("action") or "smln_pcs.asp"), data=data,
                               headers={"Referer": r2.url, "Origin": SMARTID}, timeout=self.timeout)
        r3.raise_for_status()
        m2 = re.search(r"location\.href\s*=\s*['\"]([^'\"]*loginProc[^'\"]*)", r3.text)
        if not m2:
            # 자격증명 거부(또는 알 수 없는 응답). 계정 잠금을 피하려고 이후 시도를 막는다.
            alert = re.search(r"alert\(\s*['\"](.+?)['\"]\s*\)", r3.text)
            why = alert.group(1) if alert else " ".join(
                BeautifulSoup(r3.text, "html.parser").get_text(" ", strip=True).split())[:150]
            self._blocked = (f"슈패스 로그인 거부: {why!r} — secrets/plugin_ssupath.json 확인 후 크롤러 재시작 "
                             f"(계정 잠금 방지를 위해 그때까지 로그인을 다시 시도하지 않음)")
            raise PermissionError(self._blocked)
        r4 = self._get(urljoin(r3.url, m2.group(1)), headers={"Referer": r3.url})
        if self._is_login(r4):
            raise PermissionError("SSO 인증은 통과했지만 슈패스 세션이 만들어지지 않음(loginProc) — Referer 규칙 변경?")
        self._logged_in = True

    def _fetch(self, url, params=None):
        """로그인을 보장하고 요청. 세션이 끊겨 로그인 페이지로 튕기면 재로그인 한 번."""
        if not self._logged_in:
            self._login()
        r = self._get(url, params=params)
        if self._is_login(r):
            self._logged_in = False
            self._login()
            r = self._get(url, params=params)
            if self._is_login(r):
                raise PermissionError("재로그인 후에도 로그인 페이지로 이동함")
        return r

    # ── 목록 · 상세 ──────────────────────────────────
    def _params(self, page, year):
        # 빈 값까지 전부 명시(서버가 검색조건을 세션에 기억하므로).
        return {"paginationInfo.currentPageNo": page, "sort": "0001", "chkAblyCount": "0",
                "operYySh": str(year), "operSemCdSh": "0000", "operSemCdShVal": "0000",
                "vshOrgid": "", "vshOrgzNm": "", "searchValue": "", "prgmFormCdSh": "0000",
                "eduFrDt": "", "eduToDt": "", "scpfDpmtCdSh": "", "scpfDpmtCdNm": ""}

    @staticmethod
    def _org(a):
        """카드의 운영부서(첫 번째 태그)."""
        card = a.find_parent("li")
        li = card.select_one("ul.major_type li") if card else None
        return li.get_text(strip=True) if li else ""

    def list(self, page):
        exclude = set(self.config.get("exclude_org") or [])
        y = datetime.now().year
        out, seen = [], set()
        for year in (y, y + 1, y - 1):                   # 학년도 경계 — 위 설명
            soup = BeautifulSoup(self._fetch(LIST, params=self._params(page, year)).text, "html.parser")
            for a in soup.select("a.tit.detailBtn[data-params]"):
                key = json.loads(a["data-params"]).get("encSddpbSeq")
                title = a.get_text(" ", strip=True)
                if not key or not title or key in seen:
                    continue
                seen.add(key)
                if exclude and self._org(a) in exclude:
                    continue
                out.append({"title": title, "url": f"{INFO}?encSddpbSeq={key}"})
        return out

    def detail(self, url):
        el = BeautifulSoup(self._fetch(url).text, "html.parser").select_one("#tilesContent")
        if el is None:
            raise RuntimeError("상세 본문(#tilesContent)이 없음 — 사이트 구조 변경?")
        return {"content": str(el)}
