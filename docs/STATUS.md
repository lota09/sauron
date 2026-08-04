# sauron_reborn 진행 현황

_기준: 오프라인 테스트 58/58 통과. "온디바이스 초저비용·무외부의존·누락0" 철학._

---

## ✅ 완료 (built & 오프라인 검증)

**코어 파이프라인**
- 설정주도 크롤러: 학과별 CSS 셀렉터 + fetch_type 예외(json_ssfilm/mediamba·post_lawyer·onclick_media·dom_materials). 64개 학과 시드.
- URL 차집합 신규감지 + 최초 시딩(무발송) + UPDATE_LIMIT 대량알림 가드.
- 단일 asyncio: 크롤 → 감지 → D1 즉시발송(제목+링크) → 요약큐 → D2 요약 edit.
- SQLite WAL. schema v2(kind + FK ON UPDATE CASCADE) + v3(fail_reason, Store 오픈 시 자동 이관).

**요약/LLM**
- OpenAI 호환(OlliteRT, Gemma-4-E2B-it) 스트리밍 + 모델 자동감지.
- 결정론 검증기: strip_degenerate(반복붕괴)·REFUSAL_PATTERNS(거절)·language_issue(외국문자).
- 사유별 재시도: 모델오류→폴백(무차감)/연결→대기재시도/검증실패→리롤(프롬프트 미세변형, 실측 입증). 공지당 1회, 소진 시 영구실패.
- 실패사유 DB 기록(fail_reason). 성공요약 면책문구·실패문구·내용없음문구.
- **멀티모달**: 공지에 이미지 있으면 무조건 첨부(텍스트 프롬프트 공유, 최대 4장, 다운스케일). 온디바이스 비전 동작·성능 실측(`vision_llm_experiment.md`).

**디스코드**
- 구독봇 3단계(공통→전공→기타) + 공개 상주버튼(재시작 후에도 동작) + 관리자 `/구독버튼생성` + 임베드 + 로깅.
- `setup_guild`(이름 기준 실존확인) — 실행완료, 역할/채널 63개 생성·재사용.
- 발송 라우팅: 학과채널/@everyone(실서비스) · 통합채널(--mono) · 감시채널 디버그.

**CLI/라우팅**
- 모드: `init`(시딩전용) · `run` · `redo N`(임의 재요약) · `redo "검색어"`(검색→선택→재처리).
- 직교 플래그: `--prod`(길드) · `--mono`(통합채널) · `--dryrun`.

**DB 헬퍼**: forget_url · forget_like(부분문자열) · search_notices.
**문서**: DESIGN.md · vision_llm_experiment.md · embed_gallery.html · README.

---

## 🔵 지금 검증할 차례

1. **이미지 추출 사이트별 검증** — scatch 확인됨(포스터 2장 추출 OK). **disu 재확인**(자기 셀렉터 `#printbody > div`로 재테스트, 결과 대기). 나머지 학과도 셀렉터가 본문 이미지를 잡는지 표본 점검.
2. **비전 E2E** — 이미지 공지 하나를 `redo "추가학기" --mono`로 재처리 → `summary_engine=vision:…` 확인 + 요약 품질 확인.
3. **전체 E2E(디바이스)** — `init`(시딩) → `run`/`redo` → 실LLM → 발송 → edit 까지 디버그 서버에서 한 바퀴.

---

## ⬜ 앞으로 (우선순위 순)

**A. 깨진 사이트 수리 (잘못된 URL)** — init 로그에서 확인됨:
- `lawyer`(법과대 국제법무): 404 — `lawyer.ssu.ac.kr/web/05/notice_list.do` 경로/POST 방식 재확인.
- `media`(글로벌미디어): 403 Forbidden — 헤더/차단 우회 필요.
- `ssuconvergence`(융합특성화자유전공): 도메인 소멸(DNS 실패) — 새 도메인 조사 or 비활성.

**B. 셀렉터 감사** — 학과별 content_selector가 본문·이미지를 정확히 잡는지 전수/표본 점검(disu처럼 어긋난 것 색출).

**C. ssupath 안정화** — 로그인 필요 크롤 별도 스파이크(계속 보류 중).

**D. 실서비스 준비**
- `@everyone` **실제 핑** — 현재 멘션이 임베드 필드 안이라 안 울림. message content로 옮기는 수정 필요.
- 봇의 **비공개 학과채널 접근권한** — Administrator 또는 채널별 봇 오버라이트(없으면 실채널 발송 403).
- `PROD_GUILD_ID` 입력 + 실서비스 전환.

**E. 배포/상시구동** — Note20에서 ollitert(localhost) + `run` + 봇 상시. proot/chroot, keep-alive/헬스체크, 프로세스 죽음 자동복구.

**F. 선택 개선** — 장식 이미지(작은 로고) 필터 · OCR 하이브리드 도입 여부 결정 · DESIGN.md 최신화.

---

## ⚠️ 알려진 이슈/미결
- @everyone 실제 핑 안 됨(위 D).
- 이미지 항상첨부 → 이미지 있는 공지는 멀티모달이라 ~50초+(텍스트만의 ~5초 대비 느림). 의도된 트레이드오프.
- 비전 정확도는 2B 한계(날짜·수치 오독 가능) → 면책문구로 보완, 정밀정보는 원문 링크.
- device_bash(로컬 VM)는 네트워크 없음 → 거기선 목서버 쓰는 테스트 1건 실패(실제 venv에선 통과).
