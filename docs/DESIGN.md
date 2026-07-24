# 사우론의 눈 — 재구성(sauron_reborn) 설계 문서

> 상태: 설계 확정본 v1 · 대상 기기: Galaxy Note20 Ultra 단일 온디바이스
> 전신: `sauron`(운영 중) + `ICT-project`(확장성 프로토타입)
> 이 문서는 "무엇을 왜 이렇게 짓는가"의 단일 기준점이다. 코드 착수 전 합의된 결정을 모두 담는다.

---

## 0. 설계 철학 (모든 결정의 상위 원칙)

1. **초저비용** — 유료 API·유료 인프라 0을 지향. 자원(특히 RAM)은 가장 희소한 자산.
2. **완전한 시스템 통제 / 무외부의존** — 요약·OCR·DB·웹·전송 전부 자체 기기 안에서. (단 1개의 예외: 요약 실패건 한정 Clova 폴백 — §7)
3. **온디바이스 단일 기기** — 크롤러·DB·큐·요약·OCR·디스코드 봇 전부 Note20 Ultra 내부. 별도 서버 없음.
4. **누락 0 우선** — 우선순위: **비용 최소화 > (공지 신선도 = 요약 정확도)**. "새 공지가 떴다"는 사실 전달이 요약 품질보다 중요하다.

이 철학이 아래 모든 기술 선택(SQLite, 단일 프로세스, 인프로세스 큐, Tesseract 온디맨드, 알림/요약 분리)의 근거다.

---

## 1. 하드웨어 · 토폴로지 · RAM 예산

- **전 구성요소가 Note20 Ultra 한 대 안**에서 동작한다.
- 요약 LLM은 **폰 위 추론 런타임**이 `localhost:8000/v1` (OpenAI 호환)로 노출. 모델: **Gemma 3n E2B**(기본) / E4B(승격 옵션, 컨텍스트 4k).
- 유휴 상태 기준 **7.8 / 10.3 GB** 사용 → **가용 여유 약 2.5GB**. 이 숫자가 아키텍처의 진짜 제약이다.
  - 런타임은 로드 시 `n_ctx`용 KV 캐시를 선점하므로 7.8GB엔 max-token 메모리가 이미 대부분 포함. E4B+큰 컨텍스트로 올리면 이 값이 커져 여유가 더 줄어든다 → **E2B 유지가 품질이 아니라 RAM 결정이기도 하다.**

### RAM 예산표 (여유 ~2.5GB에서)

| 구성요소 | 예상 RAM | 판정 |
|---|---|---|
| Python + asyncio + SQLite | 150~300MB | ✅ |
| 크롤러(requests + 파서) | 100~300MB | ✅ |
| discord.py 봇(게이트웨이 상시연결) | 100~200MB | ✅ |
| 디스코드 전송(REST) | 미미 | ✅ |
| **Tesseract(kor) 온디맨드** | 300~500MB 피크 | ⚠️ 아슬(로드/해제 필수) |
| PaddleOCR 상주 | 1~2GB | ❌ 위험 |
| KoBART/seq2seq + torch | 1~1.5GB | ❌ 스킵 |

**결론:** 크롤·DB·큐·봇·전송은 전부 편히 들어간다. **유일한 스윙 변수는 OCR**이고, 무거운 추가 모델(Paddle 상주/KoBART)은 얹지 않는다.

---

## 2. 전체 아키텍처

```
                 ┌───────────────── Note20 Ultra (단일 asyncio 프로세스) ─────────────────┐
                 │                                                                        │
[스케줄러 10분]──┼─▶ (B) 크롤 & 차집합 감지 ──▶ SQLite(seen_notices, notices)              │
                 │           │                                                            │
                 │           ▼  (신규 감지 즉시)                                           │
                 │      (D1) 디스코드 "제목+링크+@everyone" 발송 → message_id 저장         │
                 │           │                                                            │
                 │           ▼  queue.put(notice_id)   ← 인프로세스 인터럽트               │
                 │      ┌────────────────┐                                                │
                 │      │ asyncio.Queue  │  (+ Semaphore(1) = 폰 LLM 단일슬롯)             │
                 │      └────────────────┘                                                │
                 │           │ await queue.get()                                          │
                 │           ▼                                                            │
                 │      (C) 요약 워커: [이미지있으면 OCR] → LLM(localhost:8000) → 결과      │
                 │           │                                                            │
                 │           ▼                                                            │
                 │      (D2) 디스코드 message_id를 edit 하여 요약 삽입                      │
                 │                                                                        │
                 │  [상시] 디스코드 봇: /구독 · 버튼 → Select 메뉴 → 역할부여 + subscriptions │
                 │  [상시] keep-alive & 디버그 → 감시채널(CHANNEL_ID_DEBUG)                 │
                 └────────────────────────────────────────────────────────────────────────┘
                              LLM은 별도 런타임(같은 폰) : localhost:8000/v1
```

- **A(구독)·B(크롤)·C(요약)·D(전송)·감시** 를 한 프로세스 안 코루틴들로 구성.
- 프로세스를 쪼개지 않는 이유: RAM 절약(인터프리터·라이브러리 중복 로드 방지) + 단일 기기라 IPC 불필요.

---

## 3. 인터럽트 모델 (지난 논의의 결론)

임베디드의 하드웨어 인터럽트에 대응하는 OS 개념은 **"블록된 태스크를 이벤트가 깨우는 것"**이다. 여기서는 **DB 기능(LISTEN/NOTIFY)도 Redis도 필요 없다.** 단일 프로세스이므로:

- B가 신규 공지를 발견 → 알림 발송 후 `queue.put(notice_id)`.
- C 워커는 `await queue.get()`로 **잠들어 있다가**(CPU 0) put 시점에 깨어남 = **이것이 인터럽트**.
- LLM 호출은 `async with Semaphore(1)` 안에서 `await`. 폰이 1슬롯이므로 사실상 직렬. 응답 대기 중 코루틴은 파킹되어 자원 낭비 0.
- **확장성 확보:** Semaphore(1)의 `1`만 키우면 동시요약 확장. 훗날 서버 분리 시에만 DB 큐/LISTEN-NOTIFY로 승격.
- **크래시 복원:** 부팅 시 `notices.status IN ('notified','summarizing')` 행을 다시 큐에 적재(미완 요약 재개).

---

## 4. 신규 공지 감지 = 차집합 (핵심, 확정)

sauron이 이미 도달한 정답을 계승한다. (`Overview.py` + `Update.py`)

```
[새로 크롤한 URL 집합] − [seen_notices에 기억된 URL 집합] = [신규]
```

- **판별 키 = URL** (게시물 고유). 제목·게시위치가 아니다 → **고정공지(핀)·순서 꼬임·마감 재정렬을 자동 흡수.** → **BOLD/핀 판별 로직 불필요(폐기).**
- **UPDATE_LIMIT 가드(계승):** 한 학과에서 신규가 임계치(기본 5, 설정값) 초과면 = 사이트 구조 변경으로 전부 "신규"로 보이는 상황일 확률 ↑ → **대량 알림 차단 + 감시채널 경보.**
- **시딩(최초 1회, 우려1 해소):** 학과별 `seen_notices`가 비어 있으면 → 목록 **최대 3페이지의 URL만** 긁어 `seen_notices`에 **전량 등록(무알림)**. 이후부터 진짜 신규만 정상 처리. (최초 전량 fetch+요약이라는 고비용 회피)
  - 시딩과 UPDATE_LIMIT은 함께 동작: 시딩된 학과는 첫 정상런에서 신규가 소수여야 정상.
- `seen_notices` 테이블이 sauron의 `buffers/last-*.txt`를 대체(파일 → DB).

---

## 5. 크롤러 확장성 = "코드가 아니라 데이터로" (60+ 학과)

sauron이 60개로 못 간 이유: **사이트마다 파이썬 클래스/모델을 따로** 만들어야 했다(`DeptInfo.py` OOP + autoscraper 모델 / 일부 Selenium). ICT의 도약을 계승한다.

### 5.1 원칙
- **크롤러 코드는 하나. 사이트별 차이는 CSS 셀렉터 2개로 외부화**, DB `depts` 테이블에 저장.
  - `link_selector` : 목록 페이지에서 공지 링크(`a`) 선택
  - `content_selector` : 상세 페이지 본문 선택
- **숭실대 학과 사이트 다수가 동일 CMS** (`link_selector = tr > td.title > a`, `content_selector = #mform > table` 가 30+ 학과 반복). 셀렉터 한 쌍이 수십 학과 커버 → config-driven이 성립하는 근본 이유.
- **표준으로 안 되는 소수만 예외 핸들러**(`fetch_type`으로 분기): JSON API(ssfilm, mediamba), onclick 기반(media), POST 필요(lawyer), 특수 DOM(materials) 등 5~6개.

### 5.2 sauron autoscraper vs ICT CSS 셀렉터 — 선택
- sauron: 사이트별 `models/model_*.json`(autoscraper 학습). 예시학습식이라 편하지만 불투명·의존성(autoscraper) 추가·깨질 때 디버깅 난해.
- ICT: DB에 CSS 셀렉터. 투명·무의존·DB에서 바로 수정.
- **채택: ICT식 CSS 셀렉터(+ `fetch_type` 예외).** 무의존·저비용 철학에 부합. (autoscraper는 폐기)

### 5.3 학과 정의는 DB로 (Q48)
- `DeptInfo.py`(클래스) → **`depts` 테이블**로 이관. 학과 추가/수정이 코드 배포 없이 DB 행 편집으로.
- 초기 적재: `notificationList.csv`(ICT) + sauron `DeptInfo`의 `channel_id/icon_url/url_prefix`를 병합해 시드.

### 5.4 infocom 되살리기
`http://infocom.ssu.ac.kr` 는 학교 서버 버그(`Uncaught PDOException ... Duplicate entry ... cs_connect`)로 빈 에러페이지가 간헐 반환됨(F5로 가끔 뚫림). → **응답 본문에서 에러 시그니처(`Uncaught PDOException`/`Fatal error`) 감지 시 정상 아님으로 판정하고 짧은 간격 N회 재시도**(F5 흉내). 상당수 회복 기대.

---

## 6. OCR (이미지 공지 ~1/4)

- **트리거:** 상세 본문에 이미지가 있을 때만(ICT 방식). 없으면 바로 요약.
- **엔진:** Tesseract(kor)·PaddleOCR **둘 다 실측 벤치** 후 결정. RAM상 **온디맨드 로드 → OCR → 해제**(상주 금지).
- **폴백 계층:** OCR 실패/생략 → 이미지+제목+링크만으로도 알림은 나간다(누락 0 원칙). 이미지 공지의 요약은 best-effort.
- **보류:** PDF 첨부 공지, Gemma 3n 멀티모달(비전) — 런타임 GPU 미지원·CPU 과부하로 현재 불가. 향후 재검토(§11).

---

## 7. 요약 (LLM)

- **모델 경로:** E2B로 시작 → 품질 미달 시 E4B 승격 → 4k 토큰 초과로 실패 시 → **해당 공지 요약 포기 또는 Clova 폴백**.
- **긴 공지(4k 초과) 전략:** ① 앞부분 절단, ② 문단 청킹 후 부분요약 병합, ③ **LLM 이전 값싼 추출요약(TextRank 등)으로 선축소** 중 택. (실측 후 확정)
- **출력 포맷:** 소형모델엔 엄격 JSON을 강요하지 않는다. **느슨한 라벨 포맷**(예: `요약:` / `대상:` / `마감:`) 파싱을 기본. 구조화 필드가 임베드에 필요할 때만 최소 JSON.
  - 헛소리/거절("전 언어모델일 뿐…") 방지: 시스템 프롬프트로 "요약만 출력, 거절 금지" 고정 + 출력 검증(거절 패턴/과소길이 감지 시 실패 처리).
- **길이:** 동적 — 내용 많으면 길게, 적으면 짧게.
- **실패 정책(Q18):** 요약 실패 = **요약 포기, 알림은 유지**("새 공지 떴다"만으로 가치). → §8의 알림/요약 분리로 구현.
- **Clova 폴백(Q43):** 실패건 한정 호출. **호출 시 감시채널에 디버그 메시지**(무의존 철학의 통제된 예외임을 가시화).
- KoBART/seq2seq는 도입하지 않는다(RAM 중복, Gemma 상주 중이라 이득 없음). Gemma가 지속적으로 부적합할 때만 재검토.

---

## 8. 알림 / 요약 분리 (핵심 아키텍처 결정)

우선순위(누락 0 > 요약)를 코드에 반영한다.

1. **감지 즉시(D1):** 디스코드에 `📢 제목` + `🔗 자세히 보기` + `@everyone` 임베드 발송 → 반환된 **message_id를 notices에 저장** → `status='notified'` → `queue.put`.
2. **요약 완료 후(D2):** 저장된 message_id를 **edit(PATCH)** 하여 요약 삽입 → `status='done'`.
3. 요약 실패/타임아웃 → 메시지는 제목+링크 상태로 유지, `status='summary_failed'`.

→ **"요약 성공"이 "알림 발송"의 전제가 되지 않는다.** 임베드 포맷은 sauron `DiscordMsg.SendEmbedMessage` 계승(제목 📢, description=요약, 링크 필드 @everyone, footer=학과명+아이콘).

---

## 9. 디스코드 전송 · 구독

### 9.1 전송(팬아웃) — sauron 방식 계승
- 학과별 **채널** + 학과별 **역할(role)**. 역할 보유자에게만 채널이 보이고, 채널에서 `@everyone` = 그 채널을 볼 수 있는 전원 멘션.
- 따라서 **발송 시 사용자 목록 조회 불필요** → 사용자 수가 자원과 무관(Q33). 봇이 `depts.discord_channel_id`에 임베드 POST.
- **감시채널 이미 존재**(`CHANNEL_ID_DEBUG`): keep-alive·크롤실패·요약연속실패·Clova호출·UPDATE_LIMIT 경보 전용. 공지 채널과 분리 유지.

### 9.2 구독 = C안(디스코드 인앱, 웹 없음) — 확정
- 유저가 서버의 봇에게 `/구독`(슬래시 명령) 또는 상시 버튼으로 요청.
- 봇이 **다중선택 Select 메뉴**(학과 목록) 제시 — **ephemeral 응답**(그 유저에게만 표시) 권장. (DM은 "서버 공유+DM 허용" 조건이 걸려 마찰↑; 필요 시 DM도 지원)
- 선택 확정 → 봇이 해당 **학과 역할 부여/회수** + `users`/`subscriptions` DB 기록.
- carl-bot(리액션 롤) 완전 대체. **웹사이트 없음**(구독 창구 이상의 역할이 없으므로, Q40). 봇은 이미 서버에 있고 관리자 권한 보유 → **Manage Roles로 역할 부여 구현**(신규 학습 포인트).

---

## 10. 데이터 모델 (SQLite)

> Postgres 대신 SQLite: 별도 데몬 없음·파일 하나·최소 RAM. "DB 인터럽트"는 인프로세스 큐로 대체(§3)하므로 LISTEN/NOTIFY 불요.

```sql
-- 학과 정의 (구 DeptInfo.py / notificationList) : 코드 아닌 데이터로 관리
CREATE TABLE depts (
  dept_id            TEXT PRIMARY KEY,     -- 'cse', 'eco', ...
  name_ko            TEXT NOT NULL,
  college            TEXT, department TEXT, major TEXT,
  list_url           TEXT NOT NULL,        -- '{{page}}' 템플릿 지원
  link_selector      TEXT,                 -- 목록 링크 CSS
  content_selector   TEXT,                 -- 상세 본문 CSS
  url_prefix         TEXT DEFAULT '',
  fetch_type         TEXT DEFAULT 'html',  -- 'html'|'json_api'|'onclick'|'post'|...
  login              INTEGER DEFAULT 0,    -- ssupath 등 향후 인증 대비(현재 미사용)
  discord_channel_id TEXT,
  discord_role_id    TEXT,
  icon_url           TEXT,
  active             INTEGER DEFAULT 1
);

-- 차집합용 "기억" (sauron buffers/last-*.txt 대체)
CREATE TABLE seen_notices (
  dept_id       TEXT NOT NULL,
  url           TEXT NOT NULL,
  first_seen_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (dept_id, url)
);

-- 공지 처리 큐 겸 아카이브
CREATE TABLE notices (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id            TEXT NOT NULL,
  title              TEXT NOT NULL,
  url                TEXT UNIQUE NOT NULL,
  content_raw        TEXT,
  images_json        TEXT,                 -- [{url,filename}, ...]
  ocr_text           TEXT,
  summary            TEXT,
  status             TEXT DEFAULT 'detected', -- detected→notified→summarizing→done | summary_failed
  discord_message_id TEXT,                 -- edit 대상
  created_at         TEXT DEFAULT (datetime('now')),
  updated_at         TEXT
);

-- 구독 (원본 기록; 실제 게이팅은 디스코드 역할)
CREATE TABLE users (
  discord_user_id TEXT PRIMARY KEY,
  created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE subscriptions (
  discord_user_id TEXT NOT NULL,
  dept_id         TEXT NOT NULL,
  PRIMARY KEY (discord_user_id, dept_id)
);
```

---

## 11. 모듈 구조 (제안)

```
sauron_reborn/
├─ docs/DESIGN.md            ← 본 문서
├─ config/                   ← 상수·비밀키(secrets/) 로더
├─ db/
│   ├─ schema.sql
│   └─ store.py              ← SQLite 접근 계층(depts/seen/notices/subs)
├─ crawl/
│   ├─ scheduler.py          ← APScheduler 10분 트리거
│   ├─ fetcher.py            ← 제네릭 CSS 크롤 + fetch_type 예외(+infocom 재시도)
│   └─ diff.py               ← 차집합 + 시딩 + UPDATE_LIMIT
├─ summarize/
│   ├─ worker.py             ← asyncio 큐 소비 + Semaphore(1)
│   ├─ llm.py                ← localhost:8000/v1 클라이언트(E2B/E4B) + 검증/폴백
│   ├─ ocr.py                ← Tesseract/Paddle 온디맨드
│   └─ clova.py              ← 실패건 폴백(+감시채널 로그)
├─ discord/
│   ├─ notify.py             ← 임베드 발송(D1) + edit(D2), 감시채널
│   └─ bot.py                ← 구독 봇(슬래시/버튼/Select, 역할부여)
├─ core/
│   ├─ queue.py              ← asyncio.Queue + 크래시복원 재적재
│   └─ health.py             ← keep-alive/디버그(감시채널)
└─ main.py                   ← 단일 asyncio 프로세스 조립
```

---

## 12. 재사용 맵 (어디서 가져오나)

| 신규 모듈 | 출처 | 비고 |
|---|---|---|
| `crawl/diff.py` | sauron `Update.py`/`Overview.py` | 차집합·UPDATE_LIMIT 계승, 파일→DB |
| `crawl/fetcher.py` | ICT `tools/fetch_tool.py` | 제네릭 CSS + 예외 핸들러, infocom 재시도 추가 |
| `db/depts` 시드 | ICT `notificationList.csv` + sauron `DeptInfo.py` | 셀렉터 + channel_id/icon 병합 |
| `summarize/ocr.py` | ICT `tools/ocr_tool.py` | Paddle 코드 있음 → Tesseract 옵션 추가·온디맨드화 |
| `summarize/llm.py` | ICT `ai/SummaryContent.py` | Gemini→OpenAI호환(localhost) 교체, JSON완화 |
| `discord/notify.py` | sauron `DiscordMsg.py` | 임베드 계승 + message edit 추가 |
| `core/health.py` | sauron `CheckAlive.py`/`Errors.py` | keep-alive·디버그 그대로 |

---

## 13. 보류 / 향후 (자리만 만들어 둠)

- **ssupath(path.ssu.ac.kr) 로그인 크롤** — 로컬·GitHub 이력 어디에도 우회 코드 흔적 없음(선입견 정정). Selenium은 폰 RAM상 불가. → **별도 스파이크**로 `requests.Session` 기반 로그인 플로우(POST/토큰/쿠키) 리버스엔지니어링. `depts.login=1` + `fetch_type='login_*'` 자리를 스키마에 미리 확보. **현 릴리스 범위 제외.**
- **PDF 첨부 공지 처리** — 멀티모달/도구 확보 후.
- **Gemma 3n 비전(멀티모달)** — 런타임에서 GPU 가속 가능해지면 OCR 자체를 대체(RAM 최적). 현재 불가.
- **동시 요약 확장** — 폰 외 추론 슬롯/서버 생기면 Semaphore 값 상향 또는 DB 큐로 승격.

---

## 14. 확정된 결정 요약 (체크리스트)

- [x] 전 구성요소 온디바이스 · 단일 asyncio 프로세스
- [x] SQLite (Postgres·Redis 불사용)
- [x] 인터럽트 = 인프로세스 asyncio.Queue + Semaphore(1)
- [x] 신규감지 = URL 차집합, BOLD/핀 판별 폐기, UPDATE_LIMIT 가드, 3페이지 시딩
- [x] 크롤 = DB 저장 CSS 셀렉터 제네릭 + fetch_type 예외 (autoscraper 폐기)
- [x] 학과 정의 DeptInfo→DB(`depts`)
- [x] 요약 = 폰 Gemma E2B→E4B→(포기|Clova), JSON 완화, 동적 길이
- [x] OCR = 이미지 있을 때만, Tesseract/Paddle 온디맨드, 실패시 알림만
- [x] 알림/요약 분리 + 메시지 edit
- [x] 전송 = 학과 채널 @everyone(사용자수 무관), 감시채널 유지
- [x] 구독 = 디스코드 봇 인앱(Select/역할부여), 웹 없음
- [ ] (보류) ssupath 로그인 · PDF · 멀티모달
