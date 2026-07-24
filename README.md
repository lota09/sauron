# sauron_reborn

"사우론의 눈" 재구성. 숭실대 학과 공지를 크롤 → (신규만) 요약 → 디스코드 알림.
설계 전문은 [`docs/DESIGN.md`](docs/DESIGN.md).

- 철학: 초저비용 · 무외부의존 · 온디바이스(단일 기기) · 누락 0 우선
- 개발: Windows 11 x86 / 타겟: Note20 Ultra(chroot Ubuntu) 또는 S21(proot Ubuntu), ARM
- 요약 LLM: OpenAI 호환 엔드포인트(개발 `http://192.168.50.153:8000/v1`, 배포 시 localhost)
- 표준 asyncio + requests + bs4 만 사용(aiohttp/openai/apscheduler 불필요)

## 설치

```bash
pip install -r requirements.txt          # requests, beautifulsoup4, lxml
# OCR 쓸 때만(타겟기기): requirements.txt 주석 참고 (tesseract 권장, paddle 무거움)
cp secrets/config.json.example secrets/config.json                 # 값 수정
cp secrets/discord-api-info.json.example secrets/discord-api-info.json  # 봇 토큰(없으면 dry-run)
```

## 실행

```bash
python init/seed_db.py     # 1) DB 스키마 + 64개 학과 시드 (idempotent)
python main.py once        # 2) 부팅 재적재 + 1회 크롤 + 요약 드레인 (수동/테스트)
python main.py run         # 3) 상시: 워커 + 10분 스케줄 루프 (기본)
python main.py debug 10    # 4) 임의 10개 학과의 '최신 공지 1건'을 강제 재감지 → 실제 요약 → 결과 콘솔 출력
```

### 모드 설명

- `once` : 부팅 재적재 → **크롤 1회** → 요약 큐 비우기 → 종료. 수동 확인용.
- `run`  : 워커 상시 + `CRAWL_INTERVAL_SEC`(기본 600s)마다 크롤 반복. 실서비스.
- `debug N` : 최초 실행은 seed(제목/URL만)라 요약 결과를 볼 수 없다. 이 모드는 임의 N개 학과에서
  목록 맨 위(최신) 공지를 `seen`에서 지워 **신규처럼 재감지→콘텐츠 fetch→LLM 요약**을 태우고 결과를
  출력한다. 요약 프롬프트/품질 튜닝 반복에 사용. (겸사겸사 각 학과 크롤/셀렉터도 검증됨)

`.db` 확인: VSCode 무료 확장 **SQLite Viewer**(qwtel.sqlite-viewer)로 `db/notice.db` 더블클릭,
또는 쿼리용 **SQLite**(alexcvzz.vscode-sqlite).

`DRY_RUN=true`(또는 봇 토큰 없음)면 디스코드 전송 대신 로그만 → 안전한 테스트.
`DEBUG_EN=true`면 학과채널 대신 감시채널로 발송.

## 테스트 (오프라인, 네트워크/실LLM 불필요)

```bash
python tests/run_tests.py
```

픽스처 + 모의 LLM 서버로 검증: 크롤 파싱 · 차집합/시딩/UPDATE_LIMIT · LLM 클라이언트(정상·거절감지·E2B→E4B 승격) · run_once end-to-end(임시 DB에 공지 요약 저장). **현재 20/20 통과.**

## LLM 런타임 (OlliteRT) 참고

- 서버는 OpenAI 호환. `/v1/chat/completions` 지원 파라미터: `model, messages, stream, stream_options,
  temperature, top_p, top_k, max_tokens, stop, response_format` 등. **모르는 필드는 무시**(관대).
  `system` 롤도 지원하지만 sauron은 Gemma 호환 위해 단일 user 메시지로 보냄.
- **`LLM_MODEL`은 서버 로드 모델명과 정확히 일치**해야 함(불일치=404). 확인: `curl <base>/models`. 현재 `Gemma-4-E2B-it`.
- **prefill 30~60초**(첫 토큰까지) — `LLM_TIMEOUT` 300s 기본. 긴 공지+OCR이면 더 걸릴 수 있음.
- **thinking(사고연쇄)** 이 서버 설정에서 켜져 있으면 요약에 추론이 섞일 수 있음 → 요약용은 꺼두기.
- **비전(image) 지원**되나 CPU에서 느림 → 현재는 OCR 사용. 추후 여유 시 멀티모달 전환 여지.
- 유용 엔드포인트: `/ping`·`/health?metrics=true`(keep-alive/모니터링), `/v1/server/reload`(모델 교체).

## 구조

```
config.py          런타임 설정(env > secrets/config.json > 기본값)
main.py            진입점(once | run)
pipeline.py        오케스트레이션(crawl_pass / run_once / Components)
db/
  schema.sql       SQLite 스키마 6테이블
  store.py         DB 접근계층(스레드안전)
  notice.db        생성 결과(gitignore)
init/
  depts_seed.csv   64개 학과 시드(셀렉터·fetch_type·채널)
  seed_db.py       스키마+시드 적재(idempotent)
  generate_seed.py 시드 재생성 도구(ICT CSV → )
crawl/
  fetcher.py       제네릭 CSS + fetch_type 예외 + infocom 재시도 + 이미지
  diff.py          URL 차집합 + 시딩 + UPDATE_LIMIT
summarize/
  llm.py           OpenAI호환 요약(느슨포맷·검증·E2B→E4B→Clova)
  ocr.py           Tesseract/Paddle/Null 온디맨드 백엔드
  worker.py        요약 워커(OCR→LLM→DB→디스코드 edit)
notify/
  notifier.py      디스코드 발송(D1)+edit(D2)+감시채널, dry-run
core/
  queue.py         asyncio.Queue + Semaphore + 부팅재적재
  log.py           로거
tests/
  run_tests.py     오프라인 검증 하니스
  fixtures/        HTML 픽스처
```

## 동작 흐름

1. **스케줄러(10분)** → `crawl_pass`: 전 학과 크롤 → `diff` 차집합 신규 감지.
2. 신규 → 콘텐츠 fetch → `notices`(status=detected) → **디스코드 즉시 발송**(제목+링크+@everyone, status=notified) → 요약 큐 적재.
3. **요약 워커**: 큐에서 인터럽트식 기상 → (이미지 시 OCR) → LLM 요약(Semaphore로 동시성 제한) → DB 기록 → **디스코드 메시지 edit로 요약 삽입**(status=done).
4. 요약 실패 = 요약만 포기(알림은 유지). Clova 폴백(옵션) 시 감시채널 로그.

## 다음 단계 (예정)

- 디스코드 봇: `/구독` 슬래시 · Select 메뉴 · 역할 부여(구독=역할) — 웹 없이 인앱.
- 학과 채널/역할 **자동 생성** 자동화(수십 개).
- keep-alive/헬스체크 강화. ssupath(로그인) 별도 스파이크(보류).
