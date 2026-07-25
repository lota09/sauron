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
python init/seed_db.py     # 1) DB 스키마(v2) + 64개 학과 시드 (idempotent)
# 기존 v1 DB가 있으면(운영 데이터 보존) 먼저 이관:
python db/migrate_v2.py    #    kind 컬럼 + FK ON UPDATE CASCADE + portal→scatch_* 개명 (멱등)
python main.py once        # 2) 부팅 재적재 + 1회 크롤 + 요약 드레인 (수동/테스트)
python main.py run         # 3) 상시: 워커 + 10분 스케줄 루프 (기본 라우팅=가짜채널)
python main.py run --prod  #    운영: 실제 학과채널로 전송
python main.py redo 10     # 4) 임의 10개 학과 '최신 공지 1건' 강제 재요약 → 콘솔 출력 (선택 학과만)
```

### 모드(무엇을) 와 라우팅(어디로) — 두 축 분리

**모드:**
- `once` : 부팅 재적재 → 크롤 1회 → 요약 드레인 → 종료.
- `run`  : 워커 상시 + `CRAWL_INTERVAL_SEC`(기본 600s)마다 크롤 반복.
- `redo N` (별칭 `debug N`) : 임의 N개 학과에서 목록 맨 위(최신) 공지를 `seen`에서 지워 **신규처럼
  재요약**하고 결과를 출력. **선택된 N개 학과만** 처리(전체 크롤 안 함). 프롬프트/품질 튜닝용. 기본 10.

**라우팅 플래그(모드와 무관, 우선순위 `--dryrun` > 가짜채널 > 실채널):**
- 기본(플래그 없음) : **가짜 개발채널**(디버깅 서버 통합공지). 개발 안전 기본값.
- `--prod` : 실제 학과채널 + `@everyone`, 실서비스 서버.
- `--dryrun` : 디스코드 전송 안 함, 로그만.

`config.DEBUG_EN`(단일 식별자)이 라우팅을 결정하며 기본 True(가짜). `--prod`가 유일하게 이를 끔.
config.json엔 두지 않는다(실행 플래그로 제어).

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

## 디스코드 구독 봇 (C안, 웹 없이 인앱)

```bash
pip install -U discord.py
# secrets/config.json 에 DISCORD_GUILD_ID 설정, secrets/discord-api-info.json 에 bot_token
python -m notify.setup_guild --dry     # 무엇을 만들지 미리보기(역할+비공개채널)
python -m notify.setup_guild           # 활성 학과별 역할+비공개채널 생성 → depts에 ID 저장
python -m notify.discord_bot           # 구독 봇 상주(게이트웨이)
```

- `setup_guild.py` — 학과별 **역할 + 비공개 채널**(단과대 카테고리 아래, 역할 보유자만 열람) 생성. idempotent.
- `discord_bot.py` — `/구독` → **3단계**(kind 기준): ① **공통**(scatch 전교공지, 한 화면·학사 강조) → ② **전공**(단과대→학과, `← 다른 단과대`로 여러 단과대 반복) → ③ **기타**. 각 Select 선택은 **즉시** 역할 부여/회수 + DB 반영(부분드롭 방지). 완료 시 현황 임베드. 전부 ephemeral.
  - 다전공·공통 동시 구독을 /구독 **한 번**으로 처리(단계 분리 + 루프백 버튼). 임베드 스타일은 `docs/embed_gallery.html` H·I·J·K.
- `kind`(general/major/etc)는 `depts` 컬럼이 단일 기준(프리픽스 규칙 의존 X). general=scatch 포털 8종, major=단과대 소속, etc=그 외.
- 봇 권한: **Manage Roles / Manage Channels**, 봇 역할이 학과 역할들보다 상위여야 함.
- 순수 로직(`subscribe_logic.py`)은 오프라인 테스트됨. 게이트웨이 동작은 토큰으로 기기에서. 봇은 `logging`으로 로그인·명령·역할부여·오류를 출력.

## 다음 단계 (예정)

- keep-alive/헬스체크 강화. 실사이트 이슈(lawyer 404 / media 403 / ssuconvergence 도메인소멸) 수리.
- ssupath(로그인) 별도 스파이크(보류).
