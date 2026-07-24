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
config.DRY_RUN = True          # 디스코드 전송 → 로그
config.OCR_BACKEND = "none"
config.UPDATE_LIMIT = 5

from db.store import Store
from crawl.fetcher import Fetcher
from crawl.diff import detect_new, FetchEmpty, TooManyNew
from summarize.llm import OpenAICompatSummarizer, SummaryError
from summarize.ocr import get_ocr
from notify.notifier import Notifier
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
        store=store, fetcher=fake, ocr=get_ocr("none"),
        summarizer=OpenAICompatSummarizer(base_url=base, model="test-e2b"),
        notifier=Notifier(), queue=WorkQueue(max_concurrency=1), clova=None)

    asyncio.run(run_once(c))  # 1회차: 시딩 → 공지 0
    check("시딩회차 notices 0", len(store.recent_notices()) == 0, "")

    # 신규 2건 추가 후 재실행
    fake.list_map["testdept"] = [{"title": "새 공지 A", "url": "http://x/A"},
                                 {"title": "새 공지 B", "url": "http://x/B"}] + base_items
    c.queue = WorkQueue(max_concurrency=1)
    asyncio.run(run_once(c))
    rows = store.recent_notices()
    done = [r for r in rows if r["status"] == "done"]
    check("신규 2건 저장", len(rows) == 2, f"got {len(rows)}")
    check("요약 완료 상태", len(done) == 2, f"done {len(done)}")
    check("요약문 존재", all(r["summary"] for r in done), "")
    check("discord dry message_id 기록", all(r["discord_message_id"] for r in rows), "")
    srv.shutdown(); store.close(); os.remove(path)


def test_debug_resummarize():
    print("[test] debug 재요약(최신공지 강제 재감지)")
    from devtools import debug_resummarize
    srv, base = start_llm("ok")
    store, path = temp_store([DEPT])
    items = [{"title": f"공지{i}", "url": f"http://x/d{i}"} for i in range(3)]
    fake = FakeFetcher({"testdept": list(items)})
    c = Components(
        store=store, fetcher=fake, ocr=get_ocr("none"),
        summarizer=OpenAICompatSummarizer(base_url=base, model="test-e2b"),
        notifier=Notifier(), queue=WorkQueue(max_concurrency=1), clova=None)
    asyncio.run(run_once(c))                       # 시딩
    check("시딩 후 notices 0", len(store.recent_notices()) == 0, "")
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


if __name__ == "__main__":
    for t in (test_fetcher_parse, test_diff_seed_new_limit, test_llm_client,
              test_run_once_e2e, test_debug_resummarize, test_model_autodetect,
              test_refusal_precision, test_repetition_strip, test_language_issue):
        try:
            t()
        except Exception as e:
            import traceback
            FAIL += 1
            print(f"  ❌ {t.__name__} 예외: {e}")
            traceback.print_exc()
    print(f"\n=== 결과: PASS {PASS} / FAIL {FAIL} ===")
    sys.exit(1 if FAIL else 0)
