#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
experiments/ssupath_probe.py — 슈패스(path.ssu.ac.kr) 로그인 수집 사전검증 (브라우저 불필요, requests만).

docs/ssu_path_login_investigation.md 의 '미확인 항목'을 폰(chroot)에서 직접 메운다.
브라우저 조작이 어려운 환경이라, 남은 미지수가 전부 HTTP 수준이라는 점을 이용한다.

  python experiments/ssupath_probe.py              # T1~T5 (로그인 1회 + 필요 시 독립 세션 1회 더)
  python experiments/ssupath_probe.py --orgs       # + T6 전체 페이지 순회(운영부서 분포·총건수, 요청 ~35회)
  python experiments/ssupath_probe.py --lifetime   # + T7 세션 수명(30분·2시간 뒤 재확인, 2시간+ 걸림 → nohup 권장)

자격증명: 실행 시 getpass로 입력(셸 히스토리·파일에 안 남음). 환경변수 SSU_ID/SSU_PW가 있으면 그걸 쓴다.
안전장치:
  - 로그인 실패 시 즉시 중단(재시도 없음 — 계정 잠금 방지).
  - 읽기(GET)만. 신청/취소/저장 요청 없음. 요청 사이 1초 지연.
  - 출력·보고서에서 아이디·비밀번호·sToken·sIdno·쿠키 '값'은 가린다(쿠키는 이름만).
결과: 콘솔 + logs/ssupath_probe_<시각>.md (logs/는 git 비추적).
"""
import argparse
import getpass
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from urllib.parse import urlsplit, parse_qsl

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config  # noqa: E402  (User-Agent·로그 경로 재사용)

PATH = "https://path.ssu.ac.kr"
SMARTID = "https://smartid.ssu.ac.kr"
LIST = f"{PATH}/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do"
INFO = f"{PATH}/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmInfo.do"
LOGINCHK = f"{PATH}/comm/login/user/loginChk.do"
SMLN_PCS = f"{SMARTID}/Symtra_sso/smln_pcs.asp"
JOB_LIST = "https://job.ssu.ac.kr/service/careerProgram/careerProgramList.do?sort=0001&currentPageNo=1"

DELAY = 1.0
SECRETS = []            # 출력에서 가릴 문자열(아이디·비밀번호)
REPORT = []


def out(line=""):
    line = redact(str(line))
    print(line)
    REPORT.append(line)


def redact(s):
    for sec in SECRETS:
        if sec:
            s = s.replace(sec, "<REDACTED>")
    s = re.sub(r"(sToken|sIdno|userid|pwd|uid)=([^&\s\"']+)", r"\1=<REDACTED>", s, flags=re.I)
    return s


def shape(v):
    """값 대신 '형태'만: 길이·문자종류."""
    if v is None:
        return "-"
    kinds = []
    if re.search(r"[0-9]", v): kinds.append("숫자")
    if re.search(r"[a-z]", v): kinds.append("소문자")
    if re.search(r"[A-Z]", v): kinds.append("대문자")
    if re.search(r"[^0-9A-Za-z]", v): kinds.append("기호")
    return f"{len(v)}자({'+'.join(kinds) or '빈값'})"


def show_url(u):
    sp = urlsplit(u)
    q = "&".join(f"{k}=<{shape(v)}>" for k, v in parse_qsl(sp.query, keep_blank_values=True))
    return f"{sp.netloc}{sp.path}" + (f"?{q}" if q else "")


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT,
                      "Accept-Language": "ko-KR,ko;q=0.9"})
    return s


def get(s, url, **kw):
    time.sleep(DELAY)
    return s.get(url, timeout=(10, 30), **kw)


def is_login_page(r):
    return "/comm/login/" in r.url or "smartid.ssu.ac.kr" in r.url


def cookie_names(s):
    by = {}
    for c in s.cookies:
        by.setdefault(c.domain, []).append(f"{c.name}{'(HttpOnly)' if c.has_nonstandard_attr('HttpOnly') else ''}")
    return by


def chain(r):
    hops = list(r.history) + [r]
    for i, h in enumerate(hops):
        loc = h.headers.get("Location")
        out(f"    {i}. {h.status_code} {h.request.method} {show_url(h.url)}"
            + (f"  → Location: {show_url(requests.compat.urljoin(h.url, loc))}" if loc else ""))
        sc = h.headers.get("Set-Cookie")
        if sc:
            names = re.findall(r"(?:^|,\s*)([A-Za-z0-9_]+)=", sc)
            out(f"       Set-Cookie 이름: {sorted(set(names))}")


def mask_tokens(s):
    """JS 안의 토큰 리터럴(긴 영숫자열)·학번 모양 숫자를 가린다 — 구조(변수명·연결 방식)만 남긴다."""
    s = re.sub(r"[A-Za-z0-9+/=_%-]{40,}", lambda m: f"<TOKEN:{len(m.group())}자>", s)
    return re.sub(r"\b\d{8}\b", "<8자리숫자>", s)


def dump_handoff(soup):
    """smartid→path 넘김 방식(JS 문장·폼) 구조를 보고서에 남긴다. 값은 가림."""
    for f in soup.find_all("form"):
        out(f"  [폼] name={f.get('name')} method={f.get('method')} action={show_url(requests.compat.urljoin(SMARTID + '/', f.get('action') or ''))}"
            f" · input={[(i.get('name'), i.get('type')) for i in f.find_all('input')]}")
    for sc in soup.find_all("script"):
        code = sc.string or ""
        for ln in code.splitlines():
            if re.search(r"location|submit|loginProc|sToken|sIdno|action|href|cookie", ln, re.I):
                out(f"  [JS] {mask_tokens(ln.strip())[:220]}")


# ── T1 로그인 ────────────────────────────────────────────────────────────────
def login(s, uid, pw, label):
    out(f"\n## T1 로그인 체인 [{label}]")
    r = get(s, LIST, allow_redirects=True)
    if not is_login_page(r):
        out("  이미 로그인 상태(SSO 쿠키 재사용) — 새 세션인데 이러면 이상함")
        return True
    m = re.search(r"rtnUrl=([^&]+)", r.url)
    if not m:
        out(f"  ✗ rtnUrl 없음: {show_url(r.url)}")
        return False
    r2 = get(s, LOGINCHK, params={"rtnUrl": requests.utils.unquote(m.group(1))}, allow_redirects=True)
    out("  [a] loginChk → smartid 로그인 폼")
    chain(r2)
    if not is_login_page(r2):
        out("  (SSO 세션으로 자동 통과됨)")
        return True
    soup = BeautifulSoup(r2.text, "html.parser")
    form = soup.find("form", attrs={"name": "LoginInfo"})
    fields = {i.get("name"): (i.get("value") or "") for i in form.find_all("input")} if form else {}
    out(f"  폼 action={form.get('action') if form else None} · 필드={list(fields)}")
    data = {k: v for k, v in fields.items() if k and k not in ("chkSave",)}
    data.update({"userid": uid, "pwd": pw})
    time.sleep(DELAY)
    action = requests.compat.urljoin(r2.url, form.get("action")) if form and form.get("action") else SMLN_PCS
    r3 = s.post(action, data=data, allow_redirects=True,
                headers={"Referer": r2.url, "Origin": SMARTID}, timeout=(10, 30))
    out("  [b] smln_pcs.asp POST 이후 체인")
    chain(r3)
    # smartid가 302가 아니라 200 HTML(JS/폼)로 넘기는 경우. 1차 실행에서 확인:
    #   화면 'SSO 토큰 검증성공' → loginProc.do 로 가되 Referer 없이 가면 error_referer.jsp 로 튕긴다.
    #   → ① 응답의 JS·폼 구조를 (가려서) 보고서에 남기고 ② 폼이면 폼째 제출, 아니면 JS 주소로 가되 Referer를 붙인다.
    if "smartid.ssu.ac.kr" in r3.url:
        soup3 = BeautifulSoup(r3.text, "html.parser")
        txt = " ".join(soup3.get_text(" ", strip=True).split())[:200]
        out(f"  smartid에 머묾 · 화면 문구: {txt!r}")
        dump_handoff(soup3)
        ref = {"Referer": r3.url}
        form = next((f for f in soup3.find_all("form") if "path.ssu.ac.kr" in (f.get("action") or "")
                     or "loginProc" in (f.get("action") or "")), None)
        if form:
            act = requests.compat.urljoin(r3.url, form.get("action"))
            data = {i.get("name"): i.get("value") or "" for i in form.find_all("input") if i.get("name")}
            meth = (form.get("method") or "get").lower()
            out(f"  자동제출 폼 → {meth.upper()} {show_url(act)} · 필드={list(data)}")
            time.sleep(DELAY)
            r3 = (s.post(act, data=data, headers=ref, timeout=(10, 30)) if meth == "post"
                  else s.get(act, params=data, headers=ref, timeout=(10, 30)))
            chain(r3)
        else:
            m2 = re.search(r"(?:location\.(?:href|replace)\s*[=(]\s*|http-equiv=[\"']refresh[\"'][^>]*url=)[\"']?([^\"'>)]+)", r3.text, re.I)
            if m2:
                nxt = requests.compat.urljoin(r3.url, m2.group(1))
                out(f"  JS/meta 이동 → {show_url(nxt)} (Referer 붙임)")
                time.sleep(DELAY)
                r3 = s.get(nxt, headers=ref, allow_redirects=True, timeout=(10, 30))
                chain(r3)
    r4 = get(s, LIST, allow_redirects=True)
    ok = not is_login_page(r4) and ("로그아웃" in r4.text or "LOGOUT" in r4.text.upper())
    out(f"  결과: {'✅ 로그인 성공' if ok else '✗ 실패'} · 목록 최종 {show_url(r4.url)}")
    out(f"  쿠키(이름만): {json.dumps(cookie_names(s), ensure_ascii=False)}")
    return ok


# ── T2 목록 파싱 ─────────────────────────────────────────────────────────────
def list_page(s, page=1, sort="0001", **extra):
    # 필터는 빈 값까지 전부 명시한다. 서버가 검색조건을 세션에 기억해서, 빠뜨리면 직전 검색어가 새어 들어온다
    # (2차 실행에서 T3-b 검색 뒤 T6가 '총 2건'으로 나온 원인).
    params = {"paginationInfo.currentPageNo": page, "sort": sort, "chkAblyCount": "0",
              "operYySh": datetime.now().year, "operSemCdSh": "0000", "operSemCdShVal": "0000",
              "vshOrgid": "", "vshOrgzNm": "", "searchValue": "", "prgmFormCdSh": "0000",
              "eduFrDt": "", "eduToDt": "", "scpfDpmtCdSh": "", "scpfDpmtCdNm": "", **extra}
    r = get(s, LIST, params=params, allow_redirects=True)
    return r, BeautifulSoup(r.text, "html.parser")


def parse_items(soup):
    items = []
    for a in soup.select("a.detailBtn[data-params]"):
        t = a.get_text(" ", strip=True)
        if not t:
            continue
        try:
            key = json.loads(a["data-params"]).get("encSddpbSeq")
        except ValueError:
            key = None
        card = a.find_parent("li") or a.parent
        items.append({"title": t, "key": key, "classes": a.get("class") or [], "card": card})
    # 키별로 하나: 'tit' 클래스 링크(제목)를 우선. 없으면 가장 긴 텍스트.
    best = {}
    for it in items:
        cur = best.get(it["key"])
        score = ("tit" in it["classes"], len(it["title"]))
        if cur is None or score > ("tit" in cur["classes"], len(cur["title"])):
            best[it["key"]] = it
    order, uniq = [], []
    for it in items:
        if it["key"] not in order:
            order.append(it["key"]); uniq.append(best[it["key"]])
    return items, uniq


def t2(s):
    out("\n## T2 목록 구조")
    r, soup = list_page(s, 1)
    items, uniq = parse_items(soup)
    out(f"  a.detailBtn(텍스트O) {len(items)}개 · 고유 키 {len(uniq)}개")
    out(f"  제목 링크 class 조합: {Counter(' '.join(i['classes']) for i in items).most_common(4)}")
    out(f"  :has(span.tit) {len(soup.select('a.detailBtn:has(span.tit)'))}개 · a.tit.detailBtn {len(soup.select('a.tit.detailBtn'))}개")
    if uniq:
        card = uniq[0]["card"]
        labels = [dt.get_text(strip=True) for dt in card.select("dt")]
        out(f"  카드 라벨(dt): {labels}")
        out(f"  등록일 표기 여부: {'등록' in card.get_text()} · 키 형태: {shape(uniq[0]['key'])}")
        for it in uniq[:3]:
            out(f"    - {it['title'][:40]}")
    return uniq


# ── T3 키 안정성 ─────────────────────────────────────────────────────────────
def t3(uid, pw, first):
    out("\n## T3 encSddpbSeq 가 '서로 다른 로그인 세션'에서 같은가 (차집합 가능 여부의 핵심)")
    s2 = new_session()
    if not login(s2, uid, pw, "독립 세션 B"):
        out("  ✗ 두 번째 로그인 실패 → T3 판단 불가")
        return None
    _, soup = list_page(s2, 1)
    _, second = parse_items(soup)
    a = {i["title"]: i["key"] for i in first}
    b = {i["title"]: i["key"] for i in second}
    common = set(a) & set(b)
    same = sum(a[t] == b[t] for t in common)
    out(f"  1페이지 키 {len(first)}개 vs {len(second)}개 · 공통 제목 {len(common)}개 중 키 동일 {same}개"
        f" → {'✅ 세션 무관 고정(URL 차집합 가능)' if common and same == len(common) else '⚠ 세션마다 바뀜(URL 차집합 불가 → 대체 키 필요)'}")
    out(f"  키 순서까지 완전 일치: {[i['key'] for i in first] == [i['key'] for i in second]}")
    return s2


def t3b(s, first):
    out("\n## T3-b 같은 프로그램의 키가 job.ssu.ac.kr(공개)와 path.ssu.ac.kr(로그인)에서 같은가")
    js = BeautifulSoup(requests.get(JOB_LIST, headers={"User-Agent": config.USER_AGENT}, timeout=30).text, "html.parser")
    job = {}
    for a in js.select("a.detailBtn:has(span.tit)"):
        job[a.get_text(strip=True)] = json.loads(a["data-params"]).get("encSddpbSeq")
    title, key = next(iter(job.items()))
    _, soup = list_page(s, 1, searchValue=title)
    _, hits = parse_items(soup)
    hit = next((h for h in hits if h["title"] == title), None)
    out(f"  job 첫 항목 {title[:30]!r} → path 검색 결과 {len(hits)}건 · "
        + ("키 동일 ✅ (두 소스 교차 중복제거 가능)" if hit and hit["key"] == key else
           "키 다름/미발견 ⚠" if hit else "동일 제목 미발견"))


# ── T4 상세 / T5 이미지 ──────────────────────────────────────────────────────
def t4_t5(s, first):
    out("\n## T4 상세 본문 컨테이너")
    key = first[0]["key"]
    r = get(s, INFO, params={"encSddpbSeq": key, "paginationInfo.currentPageNo": 1})
    out(f"  {r.status_code} {show_url(r.url)} · 로그인페이지로 튕김={is_login_page(r)}")
    soup = BeautifulSoup(r.text, "html.parser")
    for sel in ("div.view-table", "table.t_view", "div.sub_wrap", "div.contents-wrap"):
        el = soup.select_one(sel)
        desc = f"{len(el.get_text(strip=True))}자 · img {len(el.find_all('img'))}" if el else "없음"
        out(f"  {sel:<18} {desc}")
    lab = soup.find(lambda e: e.name in ("th", "dt", "h3", "h4", "strong", "span", "p", "div")
                    and e.get_text(strip=True) in ("프로그램 주요내용", "주요내용"))
    out(f"  '주요내용' 라벨: {lab.name if lab else None}")
    if lab:
        for anc in list(lab.parents)[:8]:
            if anc.name in ("body", "html", "[document]"):
                break
            cls = ".".join(anc.get("class") or [])
            out(f"    조상 {anc.name}{'.' + cls if cls else ''}{('#' + anc['id']) if anc.get('id') else ''}"
                f" · {len(anc.get_text(strip=True))}자 · img {len(anc.find_all('img'))} · table {len(anc.find_all('table'))}")
        nxt = lab.find_next(["td", "dd", "div"])
        if nxt:
            out(f"    라벨 다음 칸 {nxt.name}.{'.'.join(nxt.get('class') or [])} · {len(nxt.get_text(strip=True))}자")
    for t in soup.find_all("table")[:6]:
        cap = t.caption.get_text(strip=True)[:30] if t.caption else "-"
        out(f"    table.{'.'.join(t.get('class') or [])} caption={cap!r} · {len(t.get_text(strip=True))}자")

    out("\n## T5 이미지를 로그인 쿠키 없이 열 수 있나")
    real = lambda src: src and "encSvrFileNm" in src          # 공용 placeholder(css/images) 제외
    imgs = [i.get("src") for i in soup.select("img") if real(i.get("src"))]
    for it in first[:5]:
        ci = it["card"].select_one("img")
        if ci and real(ci.get("src")):
            imgs.insert(0, ci["src"]); break
    if not imgs:
        out("  대상 이미지 없음")
    for src in imgs[:2]:
        u = requests.compat.urljoin(PATH + "/", src)
        with_c = get(s, u)
        no_c = requests.get(u, headers={"User-Agent": config.USER_AGENT}, timeout=30)
        out(f"  {show_url(u)}")
        out(f"    쿠키O {with_c.status_code} {with_c.headers.get('Content-Type')} {len(with_c.content)}B"
            f" · 쿠키X {no_c.status_code} {no_c.headers.get('Content-Type')} {len(no_c.content)}B")


# ── T6 전체 순회 ─────────────────────────────────────────────────────────────
def t6(s):
    out("\n## T6 전체 페이지 순회 (운영부서 분포·총건수·페이지당 건수)")
    orgs, per_page, keys = Counter(), [], set()
    for p in range(1, 80):
        _, soup = list_page(s, p)
        _, uniq = parse_items(soup)
        if not uniq or all(u["key"] in keys for u in uniq):
            break
        per_page.append(len(uniq))
        for u in uniq:
            keys.add(u["key"])
            first_li = u["card"].select_one("ul.major_type li")
            orgs[first_li.get_text(strip=True) if first_li else "-"] += 1
    out(f"  페이지 {len(per_page)}개 · 총 {len(keys)}건 · 페이지당 {Counter(per_page).most_common(3)}")
    out(f"  운영부서 상위: {orgs.most_common(15)}")
    out(f"  운영부서 수: {len(orgs)}")


# ── T7 세션 수명 ─────────────────────────────────────────────────────────────
def t7(s):
    out("\n## T7 세션 수명 (같은 세션 유지, 목록 재요청)")
    t0 = time.time()
    for mins in (5, 30, 60, 120):
        while time.time() - t0 < mins * 60:
            time.sleep(15)
        r = get(s, LIST, allow_redirects=True)
        hops = [h.url for h in r.history]
        auto = any("loginProc.do" in u for u in hops)
        out(f"  +{mins:>3}분: {'✗ 로그인 필요' if is_login_page(r) else ('↻ SSO 자동 재인증으로 통과' if auto else '✅ 세션 유지')}"
            f" · 리다이렉트 {len(hops)}회")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orgs", action="store_true", help="T6 전체 순회(~35 요청)")
    ap.add_argument("--lifetime", action="store_true", help="T7 세션 수명(2시간+)")
    ap.add_argument("--no-second-login", action="store_true", help="T3 독립 세션 로그인 생략")
    a = ap.parse_args()

    uid = os.environ.get("SSU_ID") or input("학번: ").strip()
    pw = os.environ.get("SSU_PW") or getpass.getpass("비밀번호(표시 안 됨): ")
    SECRETS.extend([uid, pw])

    out(f"# 슈패스 probe 결과 — {datetime.now():%Y-%m-%d %H:%M}")
    s = new_session()
    if not login(s, uid, pw, "세션 A"):
        out("\n로그인 실패 → 중단(재시도 안 함). 위 체인과 화면 문구를 확인하세요.")
        return save(1)
    first = t2(s)
    if not first:
        out("목록 파싱 0건 → 중단")
        return save(1)
    t4_t5(s, first)
    t3b(s, first)
    if not a.no_second_login:
        t3(uid, pw, first)
    if a.orgs:
        t6(s)
    if a.lifetime:
        t7(s)
    return save(0)


def save(code):
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    p = os.path.join(ROOT, "logs", f"ssupath_probe_{datetime.now():%Y%m%d_%H%M}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(REPORT) + "\n")
    print(f"\n보고서: {p}")
    return code


if __name__ == "__main__":
    sys.exit(main())
