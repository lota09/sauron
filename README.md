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
cp secrets/config.json.example secrets/config.json                 # 값 수정
cp secrets/discord-api-info.json.example secrets/discord-api-info.json  # 봇 토큰(없으면 dry-run)
```

## 실행

```bash
python init/seed_db.py                      # 1) DB 스키마(v4) + 64개 학과 '데이터'만 시드 (idempotent) — 디스코드 채널은 안 만든다
python -m notify.setup_guild --dry          # 2) (poly 발송 쓸 때) 무엇을 만들지 미리보기
python -m notify.setup_guild                #    학과별 역할·비공개 채널 + 감시채널 자동 생성 → ID를 DB에 저장
python main.py once --dst null --nosummary  # 3) 시딩: 크롤 1회로 (제목·url)만 'seeded' 기록 (무발송·무요약)
python main.py run  --dst poly              # 4) 운영: 상시 크롤 + 각 학과 채널 발송(멘션 없음)
python main.py once --dst mono              # 5) cron 근사: 크롤 1회 → 통합채널 몰빵(개발 확인, MONO_CHANNEL_ID 필요)
python main.py redo 10 --dst mono           # 6) 임의 10개 학과 '최신 공지 1건' 강제 재요약 (크롤 X, 튜닝용)
python main.py query "수강신청" --dst mono   # 7) 제목 검색 → 선택 → [재처리 | DB에서 제거]
```

> **채널은 누가 만드나?** `init/seed_db.py` 는 학과 **데이터**(셀렉터·이름·kind)만 넣는다 — 디스코드 채널/역할은 **안** 만든다.
> 채널·역할 생성은 `python -m notify.setup_guild` 몫이다. 이게 학과별 채널ID를 `depts` 에, 감시채널ID를 `app_meta` 에 저장한다.
> 그래서 **`--dst poly` 는 setup_guild 를 먼저 돌려야** 각 학과 채널로 간다(안 돌리면 채널ID가 비어 통합채널로 폴백되고 경고 로그가 뜬다).
> **첫 세팅에 사람이 넣는 값은 봇 토큰 + `DISCORD_GUILD_ID` 둘뿐** — 나머지 채널ID는 setup_guild 가 채운다.

### 모드(무엇을) 와 `--dst`(어디로) — 직교 축

**모드:**
- `run`  : 워커 상시 + `CRAWL_INTERVAL_SEC`(기본 600s)마다 크롤 반복. (기본 모드)
- `once` : 부팅 재적재 → 크롤 1회 → 요약 드레인 → 종료. cron에 걸면 실서비스 근사.
- `redo N` : 임의 N개 학과 최신 공지 1건을 `notices`에서 지워 **신규처럼 재처리**·발송. 크롤 X. 기본 10 (`redo 0` = 0건).
  옵션: `--depts a,b`(이 학과들에서만) · `--exclude a,b`(제외) · `--per K`(학과당 K건) · `--random`(1페이지에서 임의로).
  예) 새 학과 동작 확인: `python main.py redo 4 --depts job_empl,ssupath --per 2 --random --dst mono`
- `check` : 학과별 수집 점검 — 목록 1페이지 → 최신 공지 상세까지 실제로 받아 표로 보여 줌(발송·DB 기록 없음).
  목록 0건(link_selector 어긋남) · 본문 0자(content_selector 어긋남) · 30일 넘게 신규 없음(조용한 고장 의심)을 표시. `--depts a,b`로 일부만.
- `query "검색어"` : 제목에 검색어가 든 공지(시딩분 포함) 검색 → 번호 선택 → [1] 재처리 / [2] DB에서 제거.

**`--dst`(전송 대상, 택1, 기본 `null`):**
- `null`(기본) : 전송 안 함(구 dryrun). 처리·요약은 하되 디스코드로 안 보냄.
- `mono` : **통합채널**(setup_guild가 만들고 ID를 DB `app_meta`에 저장) 하나로 몰빵.
- `poly` : **각 학과 전용 채널**로 발송(멘션 없음 — 구독자는 역할로 채널을 봄).
- `<채널ID>` : 명시한 단일 채널로(무멘션).

**`--nosummary`(직교 플래그):** 요약(+상세 fetch) 생략. `--dst null` 과 함께면 **순수 시딩**, 발송 대상과 함께면 **제목+링크만** 발송(자원 절약).

빠른 개발 확인은 `--dst mono`(통합채널) 권장 — 학과별 비공개 채널은 봇에 접근권한 필요.

`.db` 확인: VSCode 무료 확장 **SQLite Viewer**(qwtel.sqlite-viewer)로 `db/notice.db` 더블클릭,
또는 쿼리용 **SQLite**(alexcvzz.vscode-sqlite).

`--dst null`(기본) 또는 봇 토큰 없음이면 디스코드 전송 대신 로그만 → 안전한 테스트.
발송처는 `--dst mono|poly|<채널ID>` 로 선택(위 라우팅 표 참고).

## 테스트 (오프라인, 네트워크/실LLM 불필요)

```bash
python tests/run_tests.py
```

픽스처 + 모의 LLM 서버로 검증: 크롤 파싱 · 차집합/시딩/UPDATE_LIMIT · LLM 클라이언트(정상·거절감지·E2B→E4B 승격) · run_once end-to-end(임시 DB에 공지 요약 저장) · `--dst` 인자 파싱/채널 라우팅 · 플러그인(가짜 SSO 서버로 슈패스 로그인) · 수집 실패 알림 · LLM 시간초과/끊김/서버오류 구분. **현재 130/130 통과.** 테스트는 토큰을 읽지 않으므로 실제 디스코드로 나가지 않는다.

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
main.py            진입점(run | once | redo | query, --dst/--nosummary)
pipeline.py        오케스트레이션(crawl_pass / run_once / Components)
devtools.py        redo(강제 재요약) / query(검색→재처리·삭제)
db/
  schema.sql       SQLite 스키마 5테이블(notices 단일화)
  store.py         DB 접근계층(스레드안전)
  notice.db        생성 결과(gitignore)
init/
  depts_seed.csv   학교 사이트 목록(학과마다 1행: 셀렉터·수집방식·설정) ← 학교마다 다른 유일한 곳
  seed_db.py       CSV 검증 → 스키마+시드 적재(idempotent)
  drop_dept.py     라이브 DB에서 학과 제거
crawl/
  fetcher.py       수집 방식 2개(html·json_api) + 범용 옵션(fetch_config) + 이미지
  diff.py          URL 차집합 + 시딩 + UPDATE_LIMIT
summarize/
  llm.py           OpenAI호환 요약(느슨포맷·검증·E2B→E4B, 이미지는 비전 입력)
  worker.py        요약 워커(이미지 준비→LLM(텍스트+비전)→DB→디스코드 edit)
notify/
  notifier.py      디스코드 발송(D1)+edit(D2)+감시채널, dry-run
core/
  queue.py         asyncio.Queue + Semaphore + 부팅재적재
  log.py           로거
tests/
  run_tests.py     오프라인 검증 하니스
  fixtures/        HTML 픽스처
```

## 새 학교에 도입

**학교마다 다른 것은 `init/depts_seed.csv` 하나뿐**이다. 코드에는 학교·사이트 이름이 없다.

1. `git clone` 후 `init/depts_seed.csv`를 자기 학교 사이트로 채운다 (학과·공지게시판마다 1행).
2. `python3 deploy.py` — venv·의존성·봇 토큰·서버 ID 입력 → CSV 검증·DB 적재 → 디스코드 역할·채널 생성 → 기존 공지 시딩(발송 없음) → (선택) 서비스 등록.
3. CSV에 문제가 있으면 `seed_db`가 **DB를 건드리기 전에** 틀린 행을 전부 보고하고 멈춘다.

### CSV 한 행 = 게시판 하나

| 열 | 필수 | 설명 |
|---|---|---|
| `dept_id` | ✓ | 영문·숫자·`_-` 슬러그. 한 번 정하면 바꾸지 말 것(공지 기록의 키) |
| `name_ko` | ✓ | 표시 이름 = 디스코드 채널·역할 이름 |
| `kind` | | `general`(홍보센터 — 전교 공지) · `major`(학과, 기본) · `etc`(기타) — 구독 화면의 단계이자 채널 카테고리 |
| `college` | | 단과대. `major`는 이 이름의 카테고리 아래 채널이 생긴다 |
| `list_url` | ✓ | 목록 주소. 페이지가 있으면 `{{page}}` 자리표시 |
| `fetch_type` | | `html`(기본) 또는 `json_api` — **수집 방식**만 있고 사이트 전용 타입은 없다 |
| `link_selector` | html ✓ | 목록에서 공지 링크(`<a>`)를 고르는 CSS |
| `content_selector` | | 상세 페이지 본문 CSS |
| `fetch_config` | | JSON. 아래 범용 옵션 |
| `icon_url` | | 알림 footer 아이콘(학교·학과 로고) |
| `seed_pages` | | 첫 실행 때 '이미 본 것'으로 기록할 페이지 수(기본 3) |

### 사이트가 평범하지 않을 때 — `fetch_config` 범용 옵션

코드를 고치지 않는다. 모든 사이트가 같은 절차를 타고, 차이는 이 설정뿐이다.

| 상황 | 설정 |
|---|---|
| 링크가 `href="#"`이고 키가 `data-*` 속성에 JSON으로 있음 | `{"link_attr": "data-params", "url_template": "view.do?seq={seq}"}` |
| 링크가 `onclick="fnView('123')"` 류 | `{"link_attr": "onclick", "link_regex": "fnView\\('(?P<id>\\d+)'\\)", "url_template": "view.do?id={id}"}` |
| 주소에 날짜 같은 매번 바뀌는 쿼리가 붙음 | `{"link_attr": "href", "link_regex": "^(?P<p>[^?#]+)", "url_template": "{p}"}` |
| 링크가 카드 전체를 감싸 날짜·뱃지가 제목에 섞임 | `{"title_selector": ".tit strong", "title_exclude": "span"}` |
| 서버가 가끔 200과 함께 PHP 에러페이지를 줌 | `{"error_page_retry": 3}` |
| 화면을 JS로 그림(목록 HTML에 공지가 없음) | `fetch_type=json_api` + 브라우저 개발자도구에서 찾은 API: `{"list_url": ".../list?page={page}", "list_path": "data.list", "id_key": "id", "title_key": "title", "url_template": "https://…/notice/{id}", "content_key": "content"}` — 목록에 본문이 없으면 `content_key` 대신 `"detail_path": "data.content"`(공지 URL이 주는 JSON에서 본문 경로) |

⚠ **`url_template` 등을 바꾸면 URL이 바뀌어 기존 공지가 전부 '신규'로 보인다.** 운영 중인 학과의 설정을 바꿀 땐 바꾸기 전후 목록 URL이 같은지 먼저 확인할 것 (다르면 `UPDATE_LIMIT`가 대량 발송을 막고 감시채널로 경보한다).

## 동작 흐름

1. **스케줄러(10분)** → `crawl_pass`: 전 학과 크롤 → `diff` 차집합 신규 감지.
2. 신규 → (제목·url) `seeded` 기록 → 콘텐츠 fetch → `notices`(status=detected) → (발송 대상이면)**디스코드 즉시 발송**(제목+링크, poly면 +@everyone, status=notified) → 요약 큐 적재.
3. **요약 워커**: 큐에서 인터럽트식 기상 → (이미지는 비전 LLM에 직접 첨부) → LLM 요약(Semaphore로 동시성 제한) → DB 기록 → **디스코드 메시지 edit로 요약 삽입**(status=done).
4. 요약 실패 = 요약만 포기(알림은 유지) + 감시채널 알림.

## 디스코드 구독 봇 (C안, 웹 없이 인앱)

```bash
pip install -U discord.py
# secrets/config.json 에 DISCORD_GUILD_ID 설정, secrets/discord-api-info.json 에 bot_token
python -m notify.setup_guild --dry     # 무엇을 만들지 미리보기(역할+비공개채널)
python -m notify.setup_guild           # 활성 학과별 역할+비공개채널 생성 → depts에 ID 저장
python -m notify.discord_bot           # 구독 봇 상주(게이트웨이)
```

- `setup_guild.py` — 학과별 **역할 + 비공개 채널**(단과대 카테고리 아래, 역할 보유자만 열람) 생성 + **감시(디버그) 채널 자동 생성**(이름 `DEBUG_CHANNEL_NAME`, 기본 `사우론-감시`) → 그 ID를 `app_meta` 에 저장. **이름으로 실존 확인**하므로 재실행해도 중복 안 만들고 재사용. idempotent.
  - 즉 감시채널 ID를 손으로 안 넣어도 된다(수동 지정은 `DEBUG_CHANNEL_ID`). 통합채널(`--dst mono`)만 curation 대상이라 `MONO_CHANNEL_ID`(또는 `--dst <채널ID>`)로 명시한다.
- `discord_bot.py` — `/구독` → **3단계**(kind 기준): ① **홍보센터**(전교 공지, 한 화면·학사 강조) → ② **전공**(단과대→학과, `← 다른 단과대`로 여러 단과대 반복) → ③ **기타**. 각 Select 선택은 **즉시** 역할 부여/회수 + DB 반영(부분드롭 방지). 완료 시 현황 임베드. 전부 ephemeral.
  - 다전공·홍보센터 동시 구독을 /구독 **한 번**으로 처리(단계 분리 + 루프백 버튼). 임베드 스타일은 `docs/embed_gallery.html` H·I·J·K.
- `kind`(general/major/etc)는 `depts` 컬럼이 단일 기준(프리픽스 규칙 의존 X). general=scatch 포털 8종, major=단과대 소속, etc=그 외.
- 봇 권한: **Manage Roles / Manage Channels**, 봇 역할이 학과 역할들보다 상위여야 함.
- 순수 로직(`subscribe_logic.py`)은 오프라인 테스트됨. 게이트웨이 동작은 토큰으로 기기에서. 봇은 `logging`으로 로그인·명령·역할부여·오류를 출력.

## 다음 단계 (예정)

- keep-alive/헬스체크 강화. 실사이트 이슈(lawyer 404 / media 403 / ssuconvergence 도메인소멸) 수리.
- ssupath(로그인) 별도 스파이크(보류).
