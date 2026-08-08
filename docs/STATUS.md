# sauron_reborn 진행 현황

_기준: 오프라인 테스트 66/66 통과. "온디바이스 초저비용·무외부의존·누락0" 철학._
_확장(나중에 할 것: API·RSS·AI에이전트봇) 평가는 [`ROADMAP.md`](ROADMAP.md)._

---

## ✅ 완료 (built & 오프라인 검증)

**코어 파이프라인**
- 설정주도 크롤러: 학과별 CSS 셀렉터 + fetch_type 예외(json_ssfilm/mediamba·post_lawyer·onclick_media·dom_materials). 64개 학과 시드.
- URL 차집합 신규감지 + 최초 시딩(무발송) + UPDATE_LIMIT 대량알림 가드.
- 단일 asyncio: 크롤 → 감지 → D1 즉시발송(제목+링크) → 요약큐 → D2 요약 edit.
- SQLite WAL. schema v4: `seen_notices`를 `notices`로 **단일 테이블화**(status: seeded→detected→notified→done/…). 시딩이 제목까지 기억 → query 검색 가능.

**요약/LLM**
- OpenAI 호환(OlliteRT, Gemma-4-E2B-it) 스트리밍 + 모델 자동감지.
- 결정론 검증기: strip_degenerate(반복붕괴)·REFUSAL_PATTERNS(거절)·language_issue(외국문자).
- 사유별 재시도: 모델오류→폴백(무차감)/연결→대기재시도/검증실패→리롤(프롬프트 미세변형, 실측 입증). 공지당 1회, 소진 시 영구실패.
- 실패사유 DB 기록(fail_reason). 성공요약 면책문구·실패문구·내용없음문구.
- **멀티모달**: 공지에 이미지 있으면 무조건 첨부(텍스트 프롬프트 공유, 최대 4장, 다운스케일). 온디바이스 비전 동작·성능 실측(`vision_llm_experiment.md`).

**디스코드**
- 구독봇 3단계(공통→전공→기타) + 공개 상주버튼(재시작 후에도 동작) + 관리자 `/구독버튼생성` + 임베드 + 로깅.
- `setup_guild`(이름 기준 실존확인) — 역할/채널 63개 생성·재사용 + **감시(디버그)채널 자동 생성**(app_meta에 ID 저장).
- 발송 라우팅: `--dst` 단일 축 — `null`(무발송)·`mono`(통합채널, `MONO_CHANNEL_ID` 명시 필수)·`poly`(학과채널+@everyone)·`<채널ID>`.
- poly인데 학과채널 미배정 시 통합채널 폴백 + **경고 로그**(조용한 오배송 방지). 감시채널은 config `DEBUG_CHANNEL_ID` 또는 setup_guild 자동생성분(app_meta).
- 첫 세팅 필수값 = **봇 토큰 + `DISCORD_GUILD_ID` 둘뿐**(나머지 채널ID는 setup_guild가 채움).

**CLI/라우팅**
- 모드: `run`(상시) · `once`(1회, cron 근사) · `redo N`(임의 재요약) · `query "검색어"`(검색→선택→재처리·삭제).
- `--dst {null|mono|poly|<채널ID>}`(택1, 기본 null) + `--nosummary`(직교: 요약·상세fetch 생략, dst null과 함께면 순수 시딩).

**DB 헬퍼**: forget_url · search_notices · delete_notice · get_meta/set_meta(app_meta).

**이미지**
- **다중 이미지 추출 버그 수정** — `_img_base`가 워드프레스 해상도 접미사(`-1568x2216`)뿐 아니라 `_숫자`(붙임 `_1/_2/_3`)까지 지워 3장짜리 공지가 1장으로 뭉개지던 것 수정. 회귀테스트 `test_image_multi_extract` 추가.
- 이미지 입력 로그: `[요약 완료] … (이미지 N/M장 입력)`(전송/추출), 로드 실패 시 `[이미지 제외]`, 상한 초과 시 `[이미지 상한]`.

**문서**: DESIGN.md · ROADMAP.md · vision_llm_experiment.md · embed_gallery.html · README · STATUS(본 문서).

---

## 🔵 지금 검증할 차례

1. **다중이미지 수정 실검증** — `query '군e러닝' --dst mono` → 재처리 → `vision:…×3` + 로그 `(이미지 3/3장 입력)` 확인(수정 전엔 ×1이었음).
2. **결정론 진단** — 같은 입력에 다른 요약 관측. `LLM_TEMPERATURE=0`으로 놓고 재현되는지 확인(서버 기본 샘플링이 비-greedy인지 판별). 비전 경로 GPU 비결정성/조용한 리롤 가능성도 함께 염두.
3. **감시채널 자동생성 검증** — `python -m notify.setup_guild` 재실행 → `사우론-감시` 채널 생성/재사용 + app_meta 저장 → 오류 시 디버그 임베드 도착 확인.
4. **이미지 추출 사이트별 감사** — scatch 확인됨. disu(`#printbody > div`) 재확인, 나머지 학과 표본 점검.
5. **전체 E2E(디바이스)** — 시딩 → `run --dst poly`(setup_guild 선행) → 실LLM → 발송 → edit 한 바퀴.

---

## ⬜ 앞으로 (우선순위 순)

**A. 깨진 사이트 수리 (잘못된 URL)** — 시딩 로그에서 확인됨:
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

**F. 선택 개선** — 장식 이미지(작은 로고) 필터 · OCR 하이브리드 도입 여부 결정 · 원본(무접미사) 이미지가 리사이즈 변형보다 "작게" 취급되는 `_img_dims` 휴리스틱 정리(현재는 1024 다운스케일이라 실害 미미).

**G. 확장(별도 트랙)** — API·RSS·AI에이전트봇 호스팅. 평가·우선순위는 [`ROADMAP.md`](ROADMAP.md)(요약: RSS 정적푸시 먼저).

---

## ⚠️ 알려진 이슈/미결
- @everyone 실제 핑 안 됨(위 D).
- 이미지 항상첨부 → 이미지 있는 공지는 멀티모달이라 ~50초+(텍스트만의 ~5초 대비 느림). 의도된 트레이드오프.
- 비전 정확도는 2B 한계(날짜·수치 오독 가능) → 면책문구로 보완, 정밀정보는 원문 링크.
- device_bash(로컬 VM)는 네트워크 없음 → 거기선 목서버 쓰는 테스트 1건 실패(실제 venv에선 통과).
