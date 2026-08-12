# -*- coding: utf-8 -*-
"""
experiments/verify_eval.py — "LLM 자기검증" 실측 하니스 v3 (자기완결, 기기에서 실행).

  python experiments/verify_eval.py [BASE_URL] [REPEATS]
  예) python experiments/verify_eval.py http://192.168.50.153:8000/v1

발견 반영:
  - 점수는 결정론적(temperature no-op, 재현됨) → 반복 불필요. REPEATS 기본 1.
  - 시간 논쟁 해결용 정밀 계측: perf_counter로 (a)워밍업(모델 로드) 시간,
    (b)TTFT(첫 토큰까지), (c)총 응답시간, (d)전체 스크립트 벽시계를 모두 출력.
  - 판정기는 '유효성 전용'(형식/길이 규칙 없음, 짧아도 감점 금지).
"""
import json
import re
import sys
import time

import requests

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://192.168.50.153:8000/v1").rstrip("/")
REPEATS = int(sys.argv[2]) if len(sys.argv) > 2 else 1
SCORE_THRESHOLD = 50
CONNECT, READ = 10, 30
clock = time.perf_counter


def detect_model():
    for path, key in (("/health", "model"), ("/models", None)):
        try:
            r = requests.get(f"{BASE_URL}{path}", timeout=(CONNECT, READ))
            if r.status_code == 200:
                j = r.json()
                if key and j.get(key):
                    return j[key]
                data = j.get("data") or []
                if data and data[0].get("id"):
                    return data[0]["id"]
        except Exception:
            pass
    return "Gemma-4-E2B-it"


MODEL = detect_model()


def chat(prompt, max_tokens=8, temperature=0.1):
    """(텍스트, TTFT초, 총초) — perf_counter 기준, 요청 직전부터 마지막 바이트까지."""
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "stream": True, "max_tokens": max_tokens, "temperature": temperature}
    t0 = clock()
    ttft = None
    text = ""
    try:
        with requests.post(f"{BASE_URL}/chat/completions", json=payload,
                           timeout=(CONNECT, READ), stream=True) as r:
            if r.status_code != 200:
                return f"HTTP {r.status_code}", 0, clock() - t0
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                tok = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                if tok:
                    if ttft is None:
                        ttft = clock() - t0
                    text += tok
    except Exception as e:
        return f"{type(e).__name__}", 0, clock() - t0
    return text.strip(), (ttft or 0), clock() - t0


BINARY_PROMPT = (
    "너는 대학 공지 요약을 검수하는 봇이다. 아래 [요약]이 학생에게 전달할 알림으로 '쓸 수 있는' 상태인지 판단하라.\n"
    "한국어 문장이 자연스럽고 의미가 통하면 적합. 요청 거절·의미 없음·같은 내용 반복·깨진 텍스트면 부적합.\n"
    "영어 고유명사·링크·이메일이 섞인 건 정상. 단, 본문 대부분이 외국어이거나 문맥과 무관한 외국어(중국어·힌디어·러시아어 등)가 뜬금없이 섞이면 부적합.\n"
    "길이가 짧아도 내용이 온전하면 적합. 형식(불릿/줄수)은 따지지 말 것.\n"
    "반드시 'OK' 또는 'FAILED' 한 단어만 출력하라.\n\n[요약]\n{s}"
)
SCORE_PROMPT = (
    "너는 대학 공지 요약을 평가하는 봇이다. 아래 [요약]이 학생에게 전달할 알림으로서 '읽을 가치'를 0~100 정수로 평가하라.\n"
    "한국어가 자연스럽고 정보가 전달되면 높게. 요청 거절·의미 없음·반복 깨짐·깨진 텍스트면 낮게.\n"
    "영어 고유명사·링크·이메일이 섞인 건 정상. 단, 본문 대부분이 외국어이거나 문맥과 무관한 외국어(중국어·힌디어·러시아어 등)가 뜬금없이 섞이면 낮게.\n"
    "길이가 짧아도 내용이 온전하면 감점 금지. 형식(불릿/줄수)은 평가에 넣지 말 것.\n"
    "반드시 숫자 하나만 출력하라.\n\n[요약]\n{s}"
)


def parse_binary(t):
    low = t.lower()
    if "failed" in low or "부적합" in t:
        return "FAILED"
    if "ok" in low or "적합" in t:
        return "OK"
    return "?"


def parse_score(t):
    m = re.search(r"\d{1,3}", t)
    return min(int(m.group()), 100) if m else None


EXAMPLES = [
    ("GOOD 장학(장문)", "good",
     "- 선발 기준일: 2026.07.21임\n- 선발 방식: 성적 순 등록금내 선발장학금임\n"
     "- 자격요건: 직전 학기 15학점 이상, 학적부성적 3.8 이상임\n"
     "- 주의사항: 졸업예정자 및 조기졸업 대상자는 선발 불가함"),
    ("GOOD 수강신청", "good",
     "- 신청 대상: 졸업 필수 과목 수강신청에 실패한 학생임.\n"
     "- 신청 기간: 9월 1일(화) 15:00 ~ 9월 3일(목) 09:59임.\n"
     "- 신청 방법: 링크를 통해 설문 작성 후 제출함."),
    ("GOOD 단문", "good",
     "- 지도교수는 saint에서 확인 가능함.\n- 미신청 학생은 학과 홈페이지 '지도교수 조회'에서 확인 바람."),
    ("GOOD 극단단문", "good",
     "- 8월 학위수여식은 8월 21일(금) 대강당에서 진행함."),
    ("BAD 거절", "bad",
     "죄송합니다. 저는 단순 언어모델일 뿐이며 해당 사이트에 접근할 권한이 없습니다."),
    ("BAD 반복붕괴", "bad",
     "- 신청 기간: 9월 1일 ~ 9월 3일임.\n- 9월 4일 15:00~17:0" + "0" * 300),
    ("BAD 무의미", "bad", "논논논 論 요약 요약 요약."),
    ("BAD 외국어", "bad",
     "This is a notice about the scholarship. Please apply before the deadline."),

    # ── 사용자 제출 추가 케이스 ──
    ("GOOD 장문(깊은중첩)", "good",
     "- 선발 기준일: 2026.07.21임\n"
     "- 선발 방식: 성적 순으로 지급되는 등록금내 선발장학금임\n"
     "- 자격요건:\n"
     "    - 국가장학금 1차 신청자: 등록금 고지서 사전감면 혜택 있음\n"
     "    - 1차 미신청자: 2차 신청/서류 제출 확인 후 지급됨\n"
     "    - 대한민국 국적 미소지자도 선발 가능함 (순수외국인전형 유학생은 불가함)\n"
     "    - 2학기 등록금 납부 예정자여야 함\n"
     "    - 전 등록휴학 후 복학 시 선발 대상에서 제외됨\n"
     "    - 직전 학기 15학점 이상 이수자 (졸업 직전학기는 12학점 이상)\n"
     "    - F학점 포함 학적부성적 직전 학기 3.8 이상임\n"
     "    - 모든 교내장학금 간 중복 수혜 불가함\n"
     "    - 졸업예정자 및 조기졸업 대상자는 선발 불가함\n"
     "- 마감일: 공지일 기준 (별도 명시 없음)\n"
     "- 대상: 상기 자격요건을 충족하는 학생임\n"
     "- 주의사항: 졸업예정자 및 조기졸업 대상자는 선발 불가함에 유의함"),

    ("BAD 반복붕괴(장문)", "bad",
     "- 신청 대상: 졸업 필수 과목 수강신청에 실패한 학생임.\n"
     "- 신청 기간: 9월 1일(화) 15:00 ~ 9월 3일(목) 09:59임.\n"
     "- 신청 방법: 링크를 통해 설문 작성 후 제출함.\n"
     "- 제출 자료: 이수구분 성적표(PDF)를 제출해야 함.\n"
     "- 신청 제한: 전공 과목만 신청 가능하며, 교양 및 타 학과 과목은 신청 불가함.\n"
     "- 기타 유의사항:\n"
     "    - 관리자 수강신청 비승인될 수 있으며, 기한 종료 후 추가 신청 불가함.\n"
     "    - 9월 4일(금) 15:00~17:0" + "0" * 700 + " 論"),

    ("GOOD 영어많은 장문", "good",
     "- 프로그램 명칭: Scholarships for talented students from all over the world\n"
     "- 지원 대상: 학사, 학·석사 통합, 석사 과정 입학생\n"
     "- 장학 혜택: 정규 학위기간 동안 월 500유로 지급\n"
     "- 지원 방법: 온라인 지원 https://scholarships.portalvs.sk/\n"
     "- 지원 마감일: 2026년 7월 31일(금) 23:59 (CEST 기준)\n"
     "- 문의처: scholarships.esif@minedu.sk\n"
     "- 제출 서류: 공식 홈페이지 및 첨부 파일 참조"),

    ("BAD 외국어혼입", "bad",   # 정상 요약에 문맥무관 외국어를 주입
     "- 대상: 4학년 대상 国际交流 디자인/미디어/아트/필름/사진/건축 전공생 पंजीकरण\n"
     "- 설명회 일시: 8월 12일(수) 오후 2~3시 конференция\n"
     "- 설명회 장소: 줌 온라인 在线会议\n"
     "- 사전등록 링크: https://iesabroad.zoom.us/webinar/register/WN_2hU1gwxZ\n"
     "- 목적: 2027년 봄학기/쿼터 방문학생 기회 안내 возможность\n"
     "- 추천 대학: 美国 UCLA, 미시시피대학, 英国 UAL, 澳大利亚 UNSW 등 университет\n"
     "- 웨비나 순서: SAF 소개, 추천 대학 안내, 동문 발표(정수인, 조규빈) धन्यवाद\n"
     "- 주요 내용: SAF 지원 방법, QnA 포함\n"
     "- 공지일: 2026.07.24"),
]


def main():
    run_t0 = clock()
    print(f"\n모델: {MODEL} @ {BASE_URL}  (반복 {REPEATS}, 점수임계 {SCORE_THRESHOLD})")

    # 워밍업: 모델 로드 비용을 steady-state와 분리
    _, w_ttft, w_tot = chat("안녕", max_tokens=1)
    print(f"워밍업(모델 로드 포함) 총 {w_tot:.1f}s (TTFT {w_ttft:.1f}s)\n")

    head = (f"{'예시':<16}{'기대':<6}{'이진':<8}{'점수':<10}{'판정':<7}"
            f"{'TTFT':<7}{'총초':<7}{'일치'}")
    print(head)
    print("-" * len(head))
    correct = 0
    for name, expect, text in EXAMPLES:
        scores, bins, ttfts, tots = [], [], [], []
        for _ in range(REPEATS):
            bt, _, bl = chat(BINARY_PROMPT.format(s=text), max_tokens=5)
            st, s_ttft, sl = chat(SCORE_PROMPT.format(s=text), max_tokens=8)
            bins.append(parse_binary(bt))
            sc = parse_score(st)
            if sc is not None:
                scores.append(sc)
            ttfts.append(s_ttft)
            tots += [bl, sl]
        avg = sum(scores) / len(scores) if scores else 0
        srng = f"{avg:.0f}" if REPEATS == 1 else f"{avg:.0f}[{min(scores)}~{max(scores)}]"
        b = max(set(bins), key=bins.count)
        verdict = "통과" if avg >= SCORE_THRESHOLD else "탈락"
        want = "통과" if expect == "good" else "탈락"
        hit = "✅" if verdict == want else "❌"
        correct += verdict == want
        print(f"{name:<16}{expect:<6}{b:<8}{srng:<10}{verdict:<7}"
              f"{sum(ttfts)/len(ttfts):<7.1f}{sum(tots)/len(tots):<7.1f}{hit}")
    print("-" * len(head))
    print(f"점수판정 정확도: {correct}/{len(EXAMPLES)}  |  전체 스크립트 {clock() - run_t0:.1f}s")
    print("TTFT=첫 토큰까지, 총초=이진·점수 호출 평균 총시간. 워밍업이 크면 idle-unload 재로드 비용.")


if __name__ == "__main__":
    main()
