# -*- coding: utf-8 -*-
"""
tests/run_tests.py — 오프라인 end-to-end 검증 (pytest 불필요).
  python tests/run_tests.py
네트워크/실LLM 없이 픽스처 + 모의 LLM 서버로 크롤파싱·차집합·시딩·UPDATE_LIMIT·LLM클라이언트·run_once 검증.
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "tests", "fixtures")

import config
config.UPDATE_LIMIT = 5

from db.store import Store
from crawl.fetcher import Fetcher
from crawl.diff import detect_new, FetchEmpty, TooManyNew
from summarize.llm import OpenAICompatSummarizer, SummaryError
from notify.notifier import Notifier
import notify.notifier as _notifier_mod
# 테스트는 절대 실제 디스코드로 나가면 안 된다. 원래는 "토큰 파일이 없으면 dry"에 기대고 있었는데,
# 봇이 도는 기기엔 토큰이 있어서 채널 "null"로 진짜 요청을 보내고 실패했다(done에 message_id 테스트).
_notifier_mod._load_token = lambda: None
from core.queue import WorkQueue
from pipeline import Components, crawl_pass, run_once

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


# ── 모의 LLM 서버 ─────────────────────────────────────
class LLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        model = body.get("model", "")
        mode = self.server.mode
        if mode == "ok":
            content = "테스트 요약입니다. 수강신청 마감은 2026년 8월 7일 17시."
        elif mode == "refuse":
            content = "죄송합니다. 저는 단순 언어모델일 뿐이며 해당 사이트에 접근할 권한이 없습니다."
        elif mode == "escalate":
            content = ("정상 요약(E4B). 마감 8월 7일." if "e4b" in model.lower()
                       else "저는 인공지능 언어모델이라 도와드릴 수 없습니다.")
        elif mode == "ai_topic":
            content = "인공지능 융합 특강을 안내합니다. 신청 마감은 2026년 8월 1일이며 대상은 전 재학생입니다."
        else:
            content = "요약"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"choices": [{"delta": {"content": content}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            out = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    def do_GET(self):
        if self.path.endswith("/models"):
            b = json.dumps({"object": "list", "data": [{"id": "Gemma-4-E2B-it", "object": "model"}]}).encode()
        elif self.path.endswith("/health"):
            b = json.dumps({"status": "ok", "model": "Gemma-4-E2B-it"}).encode()
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def start_llm(mode="ok"):
    srv = HTTPServer(("127.0.0.1", 0), LLMHandler)
    srv.mode = mode
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


# ── Fake / helpers ────────────────────────────────────
class FakeFetcher:
    def __init__(self, list_map, content=None):
        self.list_map = list_map
        self.content = content or {"content": "<p>본문 텍스트 충분히 김.</p>", "images": []}

    def scrape_list(self, dept, page=1):
        return list(self.list_map.get(dept["dept_id"], [])) if page == 1 else []

    def fetch_content(self, dept, url):
        return self.content


def temp_store(depts):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    with open(os.path.join(ROOT, "db", "schema.sql"), encoding="utf-8") as f:
        con.executescript(f.read())
    for d in depts:
        cols = ",".join(d.keys())
        ph = ",".join(["?"] * len(d))
        con.execute(f"INSERT INTO depts({cols}) VALUES ({ph})", list(d.values()))
    con.commit()
    con.close()
    return Store(path), path


DEPT = dict(dept_id="testdept", name_ko="테스트학과", list_url="http://x/list?page={{page}}",
            link_selector="tr > td.title > a", content_selector="#mform > table",
            fetch_type="html", url_prefix="", discord_channel_id="123", icon_url="")


# ── 테스트들 ──────────────────────────────────────────
def test_fetcher_parse():
    print("[test] Fetcher 파싱(픽스처)")
    f = Fetcher()
    list_html = open(os.path.join(FIX, "list_mform.html"), "rb").read()
    content_html = open(os.path.join(FIX, "content_mform.html"), "rb").read()

    class Resp:
        def __init__(self, b): self.content = b
        def raise_for_status(self): pass
        @property
        def text(self): return self.content.decode("utf-8", "ignore")

    def fake_get(url, retry_on_error_page=False):
        return Resp(list_html if "list" in url else content_html)
    f._get = fake_get

    items = f.scrape_list(DEPT, page=1)
    check("목록 3건 파싱", len(items) == 3, f"got {len(items)}")
    check("제목 추출", items[0]["title"].startswith("[필독]"), items[0]["title"])
    check("URL 절대화", items[0]["url"].startswith("http://x/"), items[0]["url"])

    detail = f.fetch_content(DEPT, "http://x/notice/view.php?idx=1005")
    check("본문 텍스트 포함", "수강신청 기간" in detail["content"], "")
    check("script 제거", "console.log" not in detail["content"], "")
    check("이미지 추출", len(detail["images"]) == 1 and detail["images"][0]["filename"].endswith(".jpg"),
          str(detail["images"]))


def test_image_multi_extract():
    print("[test] 이미지 다중 추출(_1/_2/_3 붙임 구분 + 리사이즈 변형 병합)")
    f = Fetcher()
    # 붙임 3장(_1,_2,_3). _3만 -1568x2216 리사이즈 변형/ srcset 보유(실제 scatch 패턴 근사).
    html = ('<div>'
            '<img src="https://x/up/notice_1.png">'
            '<img src="https://x/up/notice_2.png">'
            '<img src="https://x/up/notice_3-1568x2216.png" '
            'srcset="https://x/up/notice_3-768x1086.png 768w, https://x/up/notice_3-1568x2216.png 1568w">'
            '</div>')
    imgs = f._extract_images(html, "https://x/notice/view")
    urls = sorted(i["url"] for i in imgs)
    check("붙임 3개 모두 추출(_1/_2/_3)", len(imgs) == 3, str(urls))
    check("_1 보존", any(u.endswith("notice_1.png") for u in urls), str(urls))
    check("_2 보존", any(u.endswith("notice_2.png") for u in urls), str(urls))
    check("_3 리사이즈 변형은 1장으로 병합", sum("notice_3" in u for u in urls) == 1, str(urls))


def test_apiparse():
    print("[test] apiparse(dig/lexical/html unescape)")
    from crawl import apiparse
    check("dig 중첩", apiparse.dig({"a": {"b": {"c": 7}}}, "a.b.c") == 7, "")
    check("dig 실패 None", apiparse.dig({"a": 1}, "a.b.c") is None, "")
    # html: 엔티티 해제
    h = apiparse.to_html("html", "&lt;p&gt;본문&lt;img src=&quot;http://x/a.png&quot;&gt;&lt;/p&gt;")
    check("html unescape", "<p>본문" in h and 'src="http://x/a.png"' in h, h)
    # lexical: 텍스트 추출
    lex = json.dumps({"editorState": {"root": {"children": [
        {"type": "paragraph", "children": [{"type": "text", "text": "리xical본문"}]},
        {"type": "image", "src": "http://x/p.png"}]}}})
    lh = apiparse.to_html("lexical", lex)
    check("lexical 텍스트", "리xical본문" in lh, lh)
    check("lexical 이미지", 'src="http://x/p.png"' in lh, lh)


def test_json_api():
    print("[test] json_api 크롤(html·lexical, 모의응답)")
    from crawl.fetcher import Fetcher

    class FakeResp:
        def __init__(self, o): self._o = o
        def raise_for_status(self): pass
        def json(self): return self._o

    # startup류(html 본문)
    dept = {"dept_id": "t_api", "list_url": "https://x/board", "fetch_type": "json_api",
            "fetch_config": json.dumps({
                "list_url": "https://x/api/list?pageNum={page}", "list_path": "data.content.list",
                "id_key": "boardContentId", "title_key": "boardTitle", "content_key": "boardContent",
                "content_format": "html", "url_template": "https://x/board/notice/{id}", "page_base": 1})}
    payload = {"data": {"content": {"list": [
        {"boardContentId": 10, "boardTitle": "공지A",
         "boardContent": "&lt;p&gt;본문A&lt;/p&gt;&lt;img src=&quot;https://x/a.png&quot;&gt;"},
        {"boardContentId": 11, "boardTitle": "공지B", "boardContent": "&lt;p&gt;본문B&lt;/p&gt;"}]}}}
    f = Fetcher()
    f.session.get = lambda url, **kw: FakeResp(payload)
    items = f.scrape_list(dept)
    check("json_api 목록 2건", len(items) == 2, str(items))
    check("json_api 제목·URL", items[0] == {"title": "공지A", "url": "https://x/board/notice/10"}, str(items[0]))
    d = f.fetch_content(dept, "https://x/board/notice/10")
    check("json_api html 본문", "본문A" in d["content"], d["content"][:60])
    check("json_api html 이미지", len(d["images"]) == 1 and d["images"][0]["url"].endswith("a.png"), str(d["images"]))

    # media류(lexical 본문, page_base 0)
    dept2 = {"dept_id": "t_api2", "list_url": "https://m/b", "fetch_type": "json_api",
             "fetch_config": json.dumps({
                 "list_url": "https://m/v1/board/?page={page}&menuId=136", "list_path": "data.boards",
                 "id_key": "id", "title_key": "title", "content_key": "content",
                 "content_format": "lexical", "url_template": "https://m/board/notices/{id}", "page_base": 0})}
    lex = json.dumps({"editorState": {"root": {"children": [
        {"type": "paragraph", "children": [{"type": "text", "text": "미디어본문X"}]}]}}})
    payload2 = {"data": {"boards": [{"id": 5, "title": "미디어공지", "content": lex}]}}
    f2 = Fetcher()
    f2.session.get = lambda url, **kw: FakeResp(payload2)
    it2 = f2.scrape_list(dept2)
    check("json_api lexical 목록", it2 == [{"title": "미디어공지", "url": "https://m/board/notices/5"}], str(it2))
    d2 = f2.fetch_content(dept2, "https://m/board/notices/5")
    check("json_api lexical 본문", "미디어본문X" in d2["content"], d2["content"][:60])


def test_html_link_template():
    print("[test] html 링크 조립(fetch_config: link_attr·url_template·link_regex)")
    f = Fetcher()
    # 진로취업센터 실물 구조 근사: href="#", 키는 data-params(JSON). 카드마다 썸네일 링크(텍스트 없음)가 섞임.
    html = ("""<table><tr><td><a class="detailBtn" href="#" data-params='{"sjrSeq":"aa11","paginationInfo.currentPageNo":"1"}'>이마트 신입사원 모집</a></td></tr>"""
            """<tr><td><a class="detailBtn" href="#" data-params='{"sjrSeq":"bb22","paginationInfo.currentPageNo":"1"}'><img src="t.png"></a>"""
            """<a class="detailBtn" href="#" data-params='{"sjrSeq":"bb22","paginationInfo.currentPageNo":"1"}'>NH투자증권 대졸 신입</a></td></tr></table>"""
            """<a class="fn" href="javascript:void(0)" onclick="fnView('9001','A')">onclick 방식 공지 제목</a>""")

    class Resp:
        content = html.encode()
    f._get = lambda url, retry_on_error_page=False: Resp()

    base = {"dept_id": "t", "fetch_type": "html", "url_prefix": "",
            "list_url": "https://job.x/service/careerEmpl/opportunityList.do?currentPageNo={{page}}"}

    # 1) 설정 없음 = 기존 동작(href). href="#"라 전부 목록 주소로 뭉개지는 게 '기존 동작'이다 — 이게 B안이 필요한 이유.
    items = f.scrape_list({**base, "link_selector": "a.detailBtn"}, 1)
    check("설정 없으면 href 그대로(기존 동작 불변)", len({i["url"] for i in items}) == 1, str(items))

    # 2) JSON 속성 + 템플릿: 고유 URL, 썸네일 링크(텍스트 없음) 제외
    cfg = '{"link_attr": "data-params", "url_template": "opportunityInfo.do?sjrSeq={sjrSeq}"}'
    items = f.scrape_list({**base, "link_selector": "a.detailBtn", "fetch_config": cfg}, 1)
    urls = [i["url"] for i in items]
    check("data-params → 2건", len(items) == 2, str(items))
    check("상대 템플릿 → 절대 URL",
          urls[0] == "https://job.x/service/careerEmpl/opportunityInfo.do?sjrSeq=aa11", str(urls))
    check("썸네일 링크(텍스트 없음) 제외", urls.count("https://job.x/service/careerEmpl/opportunityInfo.do?sjrSeq=bb22") == 1, str(urls))

    # 3) 정규식 모드: onclick 에서 이름 있는 그룹 / 번호 그룹
    rx = {"link_attr": "onclick", "link_regex": r"fnView\('(?P<id>\d+)','(\w)'\)",
          "url_template": "/view.do?id={id}&t={1}"}
    items = f.scrape_list({**base, "link_selector": "a.fn", "fetch_config": json.dumps(rx)}, 1)
    check("onclick 정규식(이름·번호 그룹)", items == [{"title": "onclick 방식 공지 제목",
                                               "url": "https://job.x/view.do?id=9001&t=A"}], str(items))

    # 4) 템플릿 키가 속성에 없으면 조용히 누락하지 않고 실패
    bad = '{"link_attr": "data-params", "url_template": "x.do?seq={encSddpbSeq}"}'
    try:
        f.scrape_list({**base, "link_selector": "a.detailBtn", "fetch_config": bad}, 1)
        raised = False
    except Exception:
        raised = True
    check("템플릿 키 불일치 → 예외(전건 누락 방지)", raised, "")


def test_generic_options():
    print("[test] 범용 옵션(title_selector·title_exclude·error_page_retry·json_api detail_path·모르는 fetch_type)")
    f = Fetcher()
    # 카드 전체를 감싼 링크: 날짜·뱃지·미리보기가 링크 텍스트에 섞이는 구조(신소재공학과 실물 근사)
    card = ("""<div class="news-list"><ul><li><a href="/bbs/view?num=574"><div class="date_box"><p>19</p></div>"""
            """<div class="tit_box"><strong><span class="tag01">공지</span>진로지도교수 상담 신청</strong></div>"""
            """<p>미리보기 본문이 길게 이어진다</p></a></li></ul></div>""")
    calls = {"n": 0}

    class Resp:
        def __init__(self, t): self.content = t.encode(); self.text = t
        def raise_for_status(self): pass

    def fake_get(url, retry_on_error_page=0):
        return Resp(card)
    f._get = fake_get
    d = {"dept_id": "m", "fetch_type": "html", "url_prefix": "", "list_url": "https://m.x/bbs/list",
         "link_selector": ".news-list ul li > a"}
    raw = f.scrape_list(d, 1)[0]["title"]
    check("옵션 없으면 링크 전체 텍스트(날짜·뱃지·미리보기 섞임)", "19" in raw and "미리보기" in raw, raw)
    it = f.scrape_list({**d, "fetch_config": json.dumps({"title_selector": ".tit_box strong",
                                                         "title_exclude": "span"})}, 1)[0]
    check("title_selector+exclude → 제목만", it["title"] == "진로지도교수 상담 신청", it["title"])
    check("URL은 그대로 href", it["url"] == "https://m.x/bbs/view?num=574", it["url"])

    # 에러페이지 재시도: 설정 없으면 1회, error_page_retry=3이면 3회까지
    f2 = Fetcher()
    seq = ["Uncaught PDOException", "Uncaught PDOException", "<div>정상</div>"]

    class R2:
        def __init__(self, t): self.text = t; self.content = t.encode()
        def raise_for_status(self): pass
    def sess_get(url, **kw):
        calls["n"] += 1
        return R2(seq[min(calls["n"] - 1, 2)])
    f2.session.get = sess_get
    calls["n"] = 0; f2._get("http://x", retry_on_error_page=0)
    check("재시도 설정 없음 → 1회 요청", calls["n"] == 1, calls["n"])
    calls["n"] = 0; r = f2._get("http://x", retry_on_error_page=3)
    check("error_page_retry=3 → 정상 페이지까지 재시도", calls["n"] == 3 and "정상" in r.text, calls["n"])

    # json_api detail_path: 목록엔 본문이 없고 공지 URL이 상세 JSON을 준다
    f3 = Fetcher()
    def get_json(url, headers=None):
        if "list" in url:
            return {"data_list": [{"NoticeIndex": 7, "Title": "영화 공지"}]}
        return {"data_modify": {"Content": "<p>상세 본문</p><img src='/a.png'>"}}
    f3._get_json = get_json
    dj = {"dept_id": "j", "fetch_type": "json_api", "list_url": "http://j.x/",
          "fetch_config": json.dumps({"list_url": "http://j.x/list", "list_path": "data_list",
                                      "id_key": "NoticeIndex", "title_key": "Title",
                                      "url_template": "http://j.x/view?NoticeIndex={id}",
                                      "detail_path": "data_modify.Content", "content_format": "html"})}
    items = f3.scrape_list(dj, 1)
    check("json_api 목록", items == [{"title": "영화 공지", "url": "http://j.x/view?NoticeIndex=7"}], str(items))
    c = f3.fetch_content(dj, items[0]["url"])
    check("detail_path → 공지 URL의 JSON에서 본문", "상세 본문" in c["content"] and len(c["images"]) == 1, str(c)[:120])

    # 사이트 이름 붙은 옛 fetch_type은 조용히 html로 떨어지지 않고 실패
    for ft in ("json_ssfilm", "dom_materials"):
        try:
            Fetcher().scrape_list({**d, "fetch_type": ft}, 1); raised = False
        except Exception:
            raised = True
        check(f"모르는 fetch_type '{ft}' → 실패", raised, "")


def test_crawl_health():
    print("[test] 수집 실패 알림 — 상태 변화만(시작·원인변경·지속 리마인드·복구) + 예외 서식")
    import time as _t
    from core.crawl_health import CrawlHealth
    from core.errors import describe
    store, path = temp_store([DEPT])
    sent, logs = [], []

    class N:
        def debug(self, text): sent.append(text)

    def boom_net():
        try:
            raise ConnectionError("connection refused")
        except Exception as e:
            raise RuntimeError("scrape_list 실패(t p1)") from e

    def boom_key():
        try:
            {}["encSddpbSeq"]
        except Exception as e:
            raise RuntimeError("scrape_list 실패(t p1)") from e

    def err(fn):
        try:
            fn()
        except Exception as e:
            return e

    h = CrawlHealth(store, N(), logs.append, remind_sec=3600)
    for _ in range(3):
        h.failed("t", "테스트학과(t)", err(boom_net))
    check("같은 원인 3회 → 알림 1통", len(sent) == 1 and "크롤 실패" in sent[0], str(sent))
    check("알림에 원인 타입·우리 코드 위치", "원인: ConnectionError" in sent[0] and "@ tests/run_tests.py" in sent[0], sent[0])
    h.failed("t", "테스트학과(t)", err(boom_key))
    check("원인 바뀜 → 알림", len(sent) == 2 and "원인 바뀜" in sent[1] and "KeyError" in sent[1], str(sent[-1:]))

    h2 = CrawlHealth(store, N(), logs.append, remind_sec=3600)      # 크롤러 재시작
    h2.failed("t", "테스트학과(t)", err(boom_key))
    check("재시작 후에도 상태 이어짐(재알림 없음)", len(sent) == 2, str(len(sent)))
    st = h2.failing()["t"]
    check("누적 횟수 보존", st["count"] == 5, str(st))

    st["last_alert"] = _t.time() - 4000                              # 리마인드 간격 경과
    store.set_meta("crawl_fail:t", json.dumps(st))
    h2.failed("t", "테스트학과(t)", err(boom_key))
    check("간격 지나면 '여전히 실패' 1통", len(sent) == 3 and "여전히" in sent[2], str(sent[-1:]))

    h2.ok("t", "테스트학과(t)")
    check("복구 → 알림 + 상태 삭제", len(sent) == 4 and "복구" in sent[3] and not h2.failing(), str(sent[-1:]))
    h2.ok("t", "테스트학과(t)")
    check("정상 반복은 조용", len(sent) == 4, str(len(sent)))
    h2.failed("t", "테스트학과(t)", err(boom_net))
    check("복구 후 다시 실패 → 새로 알림", len(sent) == 5 and "크롤 실패" in sent[4], str(sent[-1:]))
    check("describe: 감싼 예외 → 원인까지", "원인: KeyError" in describe(err(boom_key)), describe(err(boom_key)))
    store.close(); os.remove(path)


def test_plugins():
    print("[test] 플러그인 — 수집(목록·상세·본문캐시·형식검사·오류위치) · 비밀")
    from core import plugin
    from core.errors import describe
    tmp = os.path.join(ROOT, "plugins", "zz_test_src.py")
    open(tmp, "w", encoding="utf-8").write(
        "from core.plugin import SourcePlugin\n"
        "class T(SourcePlugin):\n"
        "    calls = 0\n"
        "    def list(self, page):\n"
        "        T.calls += 1\n"
        "        if self.config.get('bad'): return [{'title': 'x'}]\n"
        "        if self.config.get('boom'): return {}['encSddpbSeq']\n"
        "        return [{'title': '본문동봉', 'url': 'http://p/1', 'content': '<p>동봉</p>'},\n"
        "                {'title': '상세필요', 'url': 'http://p/2'}]\n"
        "    def detail(self, url):\n"
        "        return {'content': '<p>상세</p><img src=\"/a.png\">'}\n")
    try:
        f = Fetcher()
        d = {"dept_id": "zt", "fetch_type": "zz_test_src", "list_url": "http://p/", "fetch_config": "{}"}
        items = f.scrape_list(d, 1); f.scrape_list(d, 2)
        check("플러그인 목록", [i["url"] for i in items] == ["http://p/1", "http://p/2"], str(items))
        check("인스턴스 유지(세션 재사용)", len(f._plugins) == 1, str(f._plugins))
        check("페이지 지원(PAGINATED)", f.paginated(d) is True, "")
        c1, c2 = f.fetch_content(d, "http://p/1"), f.fetch_content(d, "http://p/2")
        check("목록 동봉 본문 → 상세 생략", "동봉" in c1["content"], c1["content"])
        check("상세 + 이미지 추출(코어)", "상세" in c2["content"] and c2["images"][0]["url"] == "http://p/a.png", str(c2))
        for cfg, want in (('{"bad": 1}', "title·url 필요"), ('{"boom": 1}', "plugins/zz_test_src.py")):
            try:
                Fetcher().scrape_list({**d, "fetch_config": cfg}, 1); msg = ""
            except Exception as e:
                msg = describe(e)
            check(f"플러그인 오류 → '{want}'", want in msg, msg)
    finally:
        os.remove(tmp)
    try:
        Fetcher().scrape_list({**d, "fetch_type": "no_such_plugin"}, 1); raised = ""
    except Exception as e:
        raised = str(e)
    check("없는 플러그인 → 실패", "plugins/no_such_plugin.py" in raised, raised)
    # 비밀: 선언된 키가 비어 있으면 무엇이 빠졌는지
    cls = plugin.load_class("ssupath", "source")
    old = plugin.SECRETS_DIR
    plugin.SECRETS_DIR = tempfile.mkdtemp()
    try:
        check("비밀 파일 없음 → 키 목록", plugin.missing_secrets("ssupath", cls) == ["userid", "pwd"], "")
        with open(plugin.secrets_path("ssupath"), "w") as fp:
            json.dump({"userid": "2026", "pwd": ""}, fp)
        check("빈 값도 빠진 것으로", plugin.missing_secrets("ssupath", cls) == ["pwd"], "")
        try:
            plugin.create("ssupath", "source"); se = ""
        except plugin.PluginError as e:
            se = str(e)
        check("생성 시 친절한 오류", "secrets/plugin_ssupath.json" in se and "pwd" in se, se)
    finally:
        plugin.SECRETS_DIR = old


def test_ssupath_login():
    print("[test] 슈패스 플러그인 — 가짜 서버로 로그인·목록·재시도 차단")
    import plugins.ssupath as sp
    from core import plugin
    sp.DELAY = 0
    P = "https://path.ssu.ac.kr"
    LIST_HTML = ("""<ul>"""
        """<li><div class="cont_box"><ul class="major_type"><li>기계공학부</li></ul>"""
        """<a class="btn01 col08 detailBtn" data-params='{"encSddpbSeq":"k1"}'>모집중</a>"""
        """<a class="tit ellipsis detailBtn" data-params='{"encSddpbSeq":"k1"}'>피지컬AI 인턴십</a></div></li>"""
        """<li><div class="cont_box"><ul class="major_type"><li>진로취업팀</li></ul>"""
        """<a class="tit ellipsis detailBtn" data-params='{"encSddpbSeq":"k2"}'>핀테크 견학</a></div></li></ul>""")

    class Resp:
        def __init__(self, url, text=""): self.url, self.text = url, text
        def raise_for_status(self): pass

    class FakeSSO:
        def __init__(self, pw_ok=True):
            self.pw_ok, self.logged, self.posts, self.params = pw_ok, False, 0, None
        def get(self, url, params=None, headers=None, timeout=None):
            if url.startswith(P + "/comm/login/user/loginChk.do"):
                return Resp("https://smartid.ssu.ac.kr/Symtra_sso/smln.asp?apiReturnUrl=x",
                            '<form name="LoginInfo" action="smln_pcs.asp"><input name="in_tp_bit" value="0">'
                            '<input name="rqst_caus_cd" value="03"><input name="userid"><input name="pwd">'
                            '<input name="chkSave"></form>')
            if "loginProc.do" in url:
                if (headers or {}).get("Referer", "").endswith("smln_pcs.asp"):
                    self.logged = True
                    return Resp(P + "/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmList.do", LIST_HTML)
                return Resp(P + "/error_referer.jsp")
            if not self.logged:
                return Resp(P + "/comm/login/user/login.do?rtnUrl=abc123")
            self.params = params
            if params and "operYySh" in params:
                self.years = getattr(self, "years", []) + [params["operYySh"]]
            return Resp(url, LIST_HTML if "List" in url else '<div id="tilesContent"><table>상세</table></div>')
        def post(self, url, data=None, headers=None, timeout=None):
            self.posts += 1
            ok = self.pw_ok and data.get("pwd") == "right"
            return Resp("https://smartid.ssu.ac.kr/Symtra_sso/smln_pcs.asp",
                        "<script>parent.location.href = '" + P + "/comm/login/user/loginProc.do?rtnUrl=x&sIdno=1';</script>"
                        if ok else "<script>alert('비밀번호가 일치하지 않습니다.');history.back();</script>")

    cls = plugin.load_class("ssupath", "source")
    sso = FakeSSO()
    p = cls(secrets={"userid": "2026", "pwd": "right"}, config={"exclude_org": ["진로취업팀"]}, session=sso, timeout=5)
    items = p.list(1)
    check("로그인 → 목록(상태버튼 아닌 제목, 진로취업팀 제외)",
          items == [{"title": "피지컬AI 인턴십", "url": P + "/ptfol/imng/icmpNsbjtPgm/findIcmpNsbjtPgmInfo.do?encSddpbSeq=k1"}], str(items))
    check("필터를 빈 값까지 명시(세션 검색어 새어듦 방지)", sso.params.get("searchValue") == "" and sso.params["sort"] == "0001", str(sso.params))
    from datetime import datetime as _dt
    y = _dt.now().year
    check("작년·올해·내년 모두 조회(학년도 경계)", sorted(sso.years[:3]) == [str(y - 1), str(y), str(y + 1)], str(sso.years))
    check("상세 #tilesContent", "상세" in p.detail(items[0]["url"])["content"], "")
    sso.logged = False                                                     # 세션 만료
    check("세션 만료 → 재로그인 1회로 복구", len(p.list(1)) == 1 and sso.posts == 2, str(sso.posts))

    bad = FakeSSO()
    q = cls(secrets={"userid": "2026", "pwd": "wrong"}, config={}, session=bad, timeout=5)
    msgs = []
    for _ in range(3):
        try:
            q.list(1)
        except PermissionError as e:
            msgs.append(str(e))
    check("비밀번호 거부 → 사유 포함", len(msgs) == 3 and "비밀번호가 일치하지 않습니다" in msgs[0], msgs[:1])
    check("거부 후 재시도 안 함(계정 잠금 방지)", bad.posts == 1, f"POST {bad.posts}회")


def test_category_rename():
    print("[test] 카테고리 — kind별 이름(설정) · 기존 카테고리 이름 바꾸기(과반 기준·멱등)")
    import asyncio as _a
    import notify.setup_guild as sg

    class Cat:
        def __init__(self, i, name): self.id, self.name, self.edits = i, name, 0
        async def edit(self, name, reason=None):
            self.name = name; self.edits += 1; return self

    class Ch:
        def __init__(self, name, cid): self.name, self.category_id = name, cid

    orig = sg.discord.CategoryChannel
    sg.discord.CategoryChannel = Cat
    try:
        old_general, etc = Cat(1, "공통 공지"), Cat(2, "기타")
        chans = [old_general, etc, Ch("학사공지", 1), Ch("장학공지", 1), Ch("창업", 2), Ch("슈패스-비교과", 1)]
        depts = [{"dept_id": "a", "name_ko": "학사공지", "kind": "general"},
                 {"dept_id": "b", "name_ko": "장학공지", "kind": "general"},
                 {"dept_id": "c", "name_ko": "창업", "kind": "etc"},
                 {"dept_id": "d", "name_ko": "슈패스 비교과", "kind": "etc"}]   # etc인데 아직 옛 카테고리에 있음
        find = lambda n: next((c for c in chans if isinstance(c, Ch) and c.name == sg._norm_ch(n)), None)
        check("kind → 카테고리 이름(설정)", [sg._category_name(d) for d in depts] ==
              [config.GENERAL_CATEGORY_NAME] * 2 + [config.ETC_CATEGORY_NAME] * 2, "")
        sg.DRY = False
        cache = {}
        _a.run(sg._rename_categories(depts, chans, find, cache))
        check("general 과반 카테고리의 이름만 바꿈(새로 만들지 않음)",
              old_general.name == config.GENERAL_CATEGORY_NAME and old_general.edits == 1 and etc.name == "기타", old_general.name)
        check("바로 뒤 생성 단계가 재사용하도록 캐시", cache.get(config.GENERAL_CATEGORY_NAME) is old_general, str(cache))
        _a.run(sg._rename_categories(depts, chans, find, {}))
        check("두 번째 실행은 아무것도 안 함(멱등)", old_general.edits == 1, str(old_general.edits))
    finally:
        sg.discord.CategoryChannel = orig
        sg.DRY = "--dry" in sys.argv[1:]


def test_llm_timeout_vs_disconnect():
    print("[test] LLM — 읽기 시간초과(첫 토큰 대기)와 연결 끊김을 구분")
    import socket
    from summarize.llm import OpenAICompatSummarizer, ConnectionErrorLLM

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.flush()
            if self.server.mode == "slow":
                import time as _t; _t.sleep(3)                       # 첫 토큰 전 침묵 > read timeout
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n')
            else:
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"abc"}}]}\n\n'); self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)             # 생성 도중 끊음
        def log_message(self, *a): pass

    for mode, want in (("slow", "시간초과: 첫 토큰"), ("drop", "")):
        srv = HTTPServer(("127.0.0.1", 0), H); srv.mode = mode
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        s = OpenAICompatSummarizer(base_url=f"http://127.0.0.1:{srv.server_port}/v1", model="m", timeout=1)
        try:
            s._call_stream(f"http://127.0.0.1:{srv.server_port}/v1/chat/completions", {"stream": True}); msg = "(예외 없음)"
        except ConnectionErrorLLM as e:
            msg = str(e)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
        srv.shutdown()
        if mode == "slow":
            check("첫 토큰 대기 초과 → '시간초과'", want in msg, msg)
        else:
            check("생성 도중 끊김 → 시간초과로 오분류하지 않음", "시간초과" not in msg, msg)


def test_image_and_stream_errors():
    print("[test] 이미지 아님 제외 · 플러그인 세션으로 이미지 받기 · 스트림 속 서버 오류")
    import io
    from summarize.vision import to_data_url
    from summarize.llm import OpenAICompatSummarizer, ServerError
    from PIL import Image

    class R:
        def __init__(self, body, ctype): self.content, self.headers = body, {"Content-Type": ctype}
        def raise_for_status(self): pass
    class Sess:
        def __init__(self, body, ctype): self.body, self.ctype, self.calls = body, ctype, 0
        def get(self, url, **kw): self.calls += 1; return R(self.body, self.ctype)
    html = Sess(b"<!DOCTYPE HTML><html>login</html>" * 50, "text/html;charset=utf-8")
    check("HTML(로그인 페이지)은 이미지로 보내지 않음", to_data_url("http://x/download.do", session=html) is None, "")
    buf = io.BytesIO(); Image.new("RGB", (400, 300), "white").save(buf, "PNG")
    png = Sess(buf.getvalue(), "application/octet-stream")
    du = to_data_url("http://x/download.do", session=png)
    check("주어진 세션으로 받음 + 진짜 이미지는 통과", png.calls == 1 and (du or "").startswith("data:image/jpeg"), (du or "")[:30])

    f = Fetcher()
    check("내장 방식 학과는 세션 없음(로그인 없이)", f.session_for({"dept_id": "h", "fetch_type": "html"}) is None, "")

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.write(b'data: {"error":{"message":"Failed to decode image. Reason: unknown image type"}}\n\ndata: [DONE]\n\n')
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    sm = OpenAICompatSummarizer(base_url=f"http://127.0.0.1:{srv.server_port}/v1", model="m")
    try:
        sm._call_stream(f"http://127.0.0.1:{srv.server_port}/v1/chat/completions", {"stream": True}); msg = ""
    except ServerError as e:
        msg = str(e)
    srv.shutdown()
    check("스트림 속 서버 오류를 사유로(빈응답으로 뭉개지 않음)", "Failed to decode image" in msg, msg)


def test_diff_seed_new_limit():
    print("[test] 차집합 · 시딩 · UPDATE_LIMIT")
    store, path = temp_store([DEPT])
    base = [{"title": f"공지{i}", "url": f"http://x/n{i}"} for i in range(3)]
    fake = FakeFetcher({"testdept": list(base)})

    # 1) 미시딩 → seed, [] 반환
    r1 = detect_new(store, fake, DEPT)
    check("시딩 시 신규 0", r1 == [], str(r1))
    check("seeded 플래그", store.is_seeded("testdept"), "")
    check("seen 3건 기록", len(store.seen_urls("testdept")) == 3, "")

    # 2) 신규 1건 추가 → 감지(오래된→최신 순서라 리스트 맨 앞 신규가 마지막)
    fake.list_map["testdept"].insert(0, {"title": "새공지", "url": "http://x/NEW"})
    r2 = detect_new(store, fake, FakeStoreDept(store))
    check("신규 1건 감지", len(r2) == 1 and r2[0]["url"] == "http://x/NEW", str(r2))
    r2b = detect_new(store, fake, FakeStoreDept(store))
    check("재감지 없음(seen 갱신)", r2b == [], str(r2b))

    # 3) UPDATE_LIMIT 초과
    for i in range(config.UPDATE_LIMIT + 2):
        fake.list_map["testdept"].insert(0, {"title": f"폭주{i}", "url": f"http://x/B{i}"})
    raised = False
    try:
        detect_new(store, fake, FakeStoreDept(store))
    except TooManyNew:
        raised = True
    check("UPDATE_LIMIT 초과 시 TooManyNew", raised, "")
    store.close(); os.remove(path)


def FakeStoreDept(store):
    return store.get_dept("testdept")


def test_llm_client():
    print("[test] LLM 클라이언트(모의 서버)")
    srv, base = start_llm("ok")
    s = OpenAICompatSummarizer(base_url=base, model="test-e2b")
    out, eng = s.summarize("제목", "<p>수강신청 안내 본문입니다. 충분히 긴 내용.</p>")
    check("정상 요약", "요약" in out and eng == "test-e2b", out)
    srv.shutdown()

    srv, base = start_llm("refuse")
    s = OpenAICompatSummarizer(base_url=base, model="test-e2b")
    raised = False
    try:
        s.summarize("제목", "<p>본문 충분.</p>")
    except SummaryError:
        raised = True
    check("거절 감지 → SummaryError", raised, "")
    srv.shutdown()

    srv, base = start_llm("escalate")
    s = OpenAICompatSummarizer(base_url=base, model="test-e2b", fallback_model="test-e4b")
    out, eng = s.summarize("제목", "<p>본문 충분.</p>")
    check("E2B실패→E4B 승격 성공", eng == "test-e4b" and "정상" in out, f"{eng}:{out}")
    srv.shutdown()


def test_run_once_e2e():
    print("[test] run_once end-to-end (임시DB 출력)")
    srv, base = start_llm("ok")
    store, path = temp_store([DEPT])
    base_items = [{"title": f"기존{i}", "url": f"http://x/e{i}"} for i in range(2)]
    fake = FakeFetcher({"testdept": list(base_items)})
    c = Components(
        store=store, fetcher=fake,
        summarizer=OpenAICompatSummarizer(base_url=base, model="test-e2b"),
        notifier=Notifier(dst="mono"), queue=WorkQueue(max_concurrency=1))

    asyncio.run(run_once(c))  # 1회차: 전량 'seeded'(무발송)
    seeded = store.recent_notices()
    check("시딩회차 seeded 2", len(seeded) == 2 and all(r["status"] == "seeded" for r in seeded),
          f"{[(r['url'], r['status']) for r in seeded]}")

    # 신규 2건 추가 후 재실행 → 처리
    fake.list_map["testdept"] = [{"title": "새 공지 A", "url": "http://x/A"},
                                 {"title": "새 공지 B", "url": "http://x/B"}] + base_items
    c.queue = WorkQueue(max_concurrency=1)
    asyncio.run(run_once(c))
    rows = store.recent_notices()
    done = [r for r in rows if r["status"] == "done"]
    check("전체 4행(seeded2+done2)", len(rows) == 4, f"got {len(rows)}")
    check("신규 2건 요약완료", len(done) == 2, f"done {len(done)}")
    check("요약문 존재", all(r["summary"] for r in done), "")
    check("done에 message_id 기록", all(r["discord_message_id"] for r in done), "")
    srv.shutdown(); store.close(); os.remove(path)


def test_debug_resummarize():
    print("[test] debug 재요약(최신공지 강제 재감지)")
    from devtools import debug_resummarize
    srv, base = start_llm("ok")
    store, path = temp_store([DEPT])
    items = [{"title": f"공지{i}", "url": f"http://x/d{i}"} for i in range(3)]
    fake = FakeFetcher({"testdept": list(items)})
    c = Components(
        store=store, fetcher=fake,
        summarizer=OpenAICompatSummarizer(base_url=base, model="test-e2b"),
        notifier=Notifier(), queue=WorkQueue(max_concurrency=1))
    asyncio.run(run_once(c))                       # 시딩(통합테이블: 전량 'seeded' 행)
    seeded = store.recent_notices()
    check("시딩 후 seeded 3", len(seeded) == 3 and all(r["status"] == "seeded" for r in seeded),
          f"{[(r['url'], r['status']) for r in seeded]}")
    c.queue = WorkQueue(max_concurrency=1)
    asyncio.run(debug_resummarize(c, 1))           # 최신 1건 강제 재요약
    rows = store.recent_notices()
    done = [r for r in rows if r["url"] == items[0]["url"] and r["status"] == "done" and r["summary"]]
    check("최신공지 재요약 완료", len(done) == 1, f"rows={[(r['url'], r['status']) for r in rows]}")
    srv.shutdown(); store.close(); os.remove(path)


def test_model_autodetect():
    print("[test] 모델 자동감지(auto → /v1/models)")
    from summarize.llm import fetch_loaded_model, OpenAICompatSummarizer
    srv, base = start_llm("ok")
    check("fetch_loaded_model", fetch_loaded_model(base) == "Gemma-4-E2B-it", "")
    s = OpenAICompatSummarizer(base_url=base, model="auto")
    check("ensure_model 확정", s.ensure_model() == "Gemma-4-E2B-it", s.model)
    out, eng = s.summarize("제목", "<p>본문 충분히 김.</p>")
    check("auto 모델로 요약", eng == "Gemma-4-E2B-it" and out, f"{eng}:{out}")
    srv.shutdown()


def test_repetition_strip():
    print("[test] 반복 붕괴 제거(strip_degenerate)")
    from summarize.llm import strip_degenerate
    good = "- 신청 대상: 재학생임.\n- 마감: 8월 7일 17시임.\n- 방법: u-SAINT 신청함."
    # 문자 반복 붕괴
    bad = good + "\n- 9월 4일 15:00~17:0" + "0" * 200
    cleaned, cut = strip_degenerate(bad)
    check("문자반복 잘림", cut and "0" * 30 not in cleaned, "")
    check("앞부분 요약 보존", "신청 대상" in cleaned and "마감" in cleaned, "")
    # 정상 텍스트는 안 건드림
    c2, cut2 = strip_degenerate(good)
    check("정상은 미변형", (not cut2) and c2.strip() == good.strip(), f"cut2={cut2}")
    # 동일 줄 반복
    dup = "- A임.\n- 같은줄임.\n- 같은줄임.\n- 같은줄임.\n- 같은줄임."
    c3, cut3 = strip_degenerate(dup)
    check("동일줄 반복 축소", cut3 and c3.count("같은줄") <= 2, c3)


def test_language_issue():
    print("[test] 언어 이탈 결정론 검사(2B judge가 못 잡는 것)")
    from summarize.llm import language_issue
    good_ko = "- 선발 기준일: 2026.07.21임\n- 자격요건: 직전 학기 15학점 이상임\n- 주의사항: 졸업예정자 선발 불가함"
    good_en = ("- 프로그램 명칭: Scholarships for talented students from all over the world\n"
               "- 지원 방법: 온라인 지원 https://scholarships.portalvs.sk/\n- 문의처: scholarships.esif@minedu.sk")
    bad_mix = "- 대상: 4학년 国际交流 전공생 पंजीकरण\n- 일시: 8월 12일 конференция\n- 추천: 美国 UCLA 등 университет"
    bad_en = "This is a notice about the scholarship. Please apply before the deadline. Thank you."
    check("순한국어 통과", language_issue(good_ko) is None, "")
    check("영어많은 정상 통과(오탐 X)", language_issue(good_en) is None, str(language_issue(good_en)))
    check("외국문자 혼입 차단", language_issue(bad_mix) is not None, "")
    check("순영어 차단", language_issue(bad_en) is not None, "")


def test_refusal_precision():
    print("[test] 거절감지 정밀도(정상 AI공지 통과 · 실제 거절 차단)")
    srv, base = start_llm("ai_topic")
    s = OpenAICompatSummarizer(base_url=base, model="test-e2b")
    out, _ = s.summarize("인공지능 특강", "<p>인공지능 융합 특강 신청 안내. 충분한 본문.</p>")
    check("AI주제 요약 통과(오탐 X)", bool(out) and "인공지능" in out, out)
    srv.shutdown()
    srv, base = start_llm("refuse")
    s = OpenAICompatSummarizer(base_url=base, model="test-e2b")
    raised = False
    try:
        s.summarize("t", "<p>본문 충분.</p>")
    except SummaryError:
        raised = True
    check("실제 거절은 차단 유지", raised, "")
    srv.shutdown()


def test_dst_routing():
    print("[test] --dst 인자 파싱 · 채널 라우팅")
    import main
    P = main._parse_args
    # 파싱: 반환 (mode, num, query, dst, nosummary)
    check("run 기본(dst null)", P(["m", "run"]) == ("run", None, None, "null", False), str(P(["m", "run"])))
    check("dst 미지정→null", P(["m", "once"]) == ("once", None, None, "null", False), str(P(["m", "once"])))
    check("once --dst null --nosummary(=시딩)",
          P(["m", "once", "--dst", "null", "--nosummary"]) == ("once", None, None, "null", True), "")
    check("once --dst poly", P(["m", "once", "--dst", "poly"]) == ("once", None, None, "poly", False), "")
    check("--dst=mono 등호형", P(["m", "run", "--dst=mono"]) == ("run", None, None, "mono", False), "")
    check("redo 4 --dst mono", P(["m", "redo", "4", "--dst", "mono"]) == ("redo", 4, None, "mono", False), "")
    check("redo 0 명시(0 존중)", P(["m", "redo", "0"]) == ("redo", 0, None, "null", False), str(P(["m", "redo", "0"])))
    check("채널ID 직접 지정",
          P(["m", "run", "--dst", "1530567154473373837"]) == ("run", None, None, "1530567154473373837", False), "")
    check("query 검색어", P(["m", "query", "수강신청", "--dst", "mono"]) == ("query", None, "수강신청", "mono", False), "")
    # 라우팅: dst별 발송 채널(멘션은 전 경로에서 제거됨)
    dept = {"discord_channel_id": "REAL", "discord_role_id": "R9"}
    chp = Notifier(dst="poly")._resolve_channel(dept)
    check("poly→학과채널", chp == "REAL", str(chp))
    chm = Notifier(dst="mono", mono_channel_id="MONO")._resolve_channel(dept)
    check("mono→통합채널(주입)", chm == "MONO", str(chm))
    chid = Notifier(dst="1530567154473373837")._resolve_channel(dept)
    check("채널ID→해당채널", chid == "1530567154473373837", str(chid))
    chn = Notifier(dst="null")._resolve_channel(dept)
    check("null→채널없음", chn is None, str(chn))
    check("null send_enabled False", Notifier(dst="null").send_enabled is False, "")
    check("mono send_enabled True", Notifier(dst="mono").send_enabled is True, "")


def test_subscribe_logic():
    print("[test] 구독 로직 + DB")
    from notify.subscribe_logic import group_by_college, dept_select_options, diff_for_subset
    depts = [
        {"dept_id": "cse", "name_ko": "컴퓨터학부", "college": "IT대학", "discord_role_id": "R1"},
        {"dept_id": "sw", "name_ko": "소프트웨어학부", "college": "IT대학", "discord_role_id": "R2"},
        {"dept_id": "eco", "name_ko": "경제학과", "college": "경제통상대학", "discord_role_id": "R3"},
    ]
    g = group_by_college(depts)
    check("단과대 그룹핑", list(g) == ["IT대학", "경제통상대학"] and len(g["IT대학"]) == 2, str(list(g)))
    opts, dropped = dept_select_options(g["IT대학"], subscribed_ids=["cse"])
    check("현재 구독 기본선택", opts[0]["default"] is True and opts[1]["default"] is False, str(opts))
    # 25 초과 잘림
    many = [{"dept_id": f"d{i}", "name_ko": f"n{i}"} for i in range(30)]
    _, dr = dept_select_options(many, [])
    check("25 초과 잘림 보고", dr == 5, f"dropped={dr}")
    # subset diff: IT대학에서 sw 선택, cse 해제
    diff = diff_for_subset(["cse", "sw"], selected_ids=["sw"], current_ids=["cse", "eco"])
    check("subset add/remove", diff == {"add": ["sw"], "remove": ["cse"]}, str(diff))
    check("subset 밖(eco) 불변", "eco" not in diff["add"] + diff["remove"], "")

    # DB 구독 메서드
    store, path = temp_store([dict(dept_id="cse", name_ko="컴퓨터학부", list_url="http://x")])
    store.add_subscription("U1", "cse")
    check("구독 추가", store.user_subscriptions("U1") == ["cse"], "")
    store.add_subscription("U1", "cse")  # 중복 무시
    check("중복 무시", store.user_subscriptions("U1") == ["cse"], "")
    store.remove_subscription("U1", "cse")
    check("구독 해제", store.user_subscriptions("U1") == [], "")
    store.set_dept_discord("cse", channel_id="C9", role_id="R9")
    d = store.get_dept("cse")
    check("채널/역할 ID 저장", d["discord_channel_id"] == "C9" and d["discord_role_id"] == "R9", "")
    # app_meta 라운드트립(setup_guild 자동생성 감시채널ID 저장 경로)
    check("get_meta 기본값", store.get_meta("debug_channel_id", "X") == "X", "")
    store.set_meta("debug_channel_id", "1234567890")
    check("set/get_meta 저장", store.get_meta("debug_channel_id") == "1234567890", "")
    store.set_meta("debug_channel_id", "999")  # upsert 덮어쓰기
    check("set_meta upsert", store.get_meta("debug_channel_id") == "999", "")
    store.close(); os.remove(path)


if __name__ == "__main__":
    for t in (test_fetcher_parse, test_image_multi_extract, test_apiparse, test_json_api, test_html_link_template, test_generic_options, test_plugins, test_ssupath_login, test_category_rename,
              test_diff_seed_new_limit, test_crawl_health, test_llm_client, test_llm_timeout_vs_disconnect, test_image_and_stream_errors,
              test_run_once_e2e, test_debug_resummarize, test_model_autodetect,
              test_refusal_precision, test_repetition_strip, test_language_issue,
              test_subscribe_logic, test_dst_routing):
        try:
            t()
        except Exception as e:
            import traceback
            FAIL += 1
            print(f"  ❌ {t.__name__} 예외: {e}")
            traceback.print_exc()
    print(f"\n=== 결과: PASS {PASS} / FAIL {FAIL} ===")
    sys.exit(1 if FAIL else 0)
