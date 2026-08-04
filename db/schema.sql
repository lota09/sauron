-- sauron_reborn SQLite schema  (DESIGN.md §10)
-- SQLite: 단일 파일, 무데몬, 최소 RAM. "DB 인터럽트"는 인프로세스 큐로 대체.
-- 개발=Windows x86 / 타겟=ARM(chroot·proot Ubuntu 24.04). OS·경로 의존 없음.

PRAGMA journal_mode = WAL;      -- 크롤(쓰기)과 봇/조회(읽기) 동시성 향상
PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────
-- 학과 정의 (구 DeptInfo.py / ICT notificationList) : "코드가 아니라 데이터"
-- 학과 추가/수정 = 코드 배포 없이 이 테이블 행 편집
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS depts (
  dept_id            TEXT PRIMARY KEY,      -- 안정 슬러그. 예: 'cse', 'eco', 'scatch_haksa'
  name_ko            TEXT NOT NULL,         -- 학과/카테고리 표시명
  kind               TEXT NOT NULL DEFAULT 'major',
                     -- 구독 분류(봇 3단계 기준). 'general'(전교공통) | 'major'(전공) | 'etc'(기타)
                     --  general = scatch 포털 공통(학사·장학 등), major = 단과대 소속 학과, etc = 그 외
  college            TEXT,                  -- 단과대
  department         TEXT,                  -- 학부
  major              TEXT,                  -- 전공(있으면)
  list_url           TEXT NOT NULL,         -- 목록 URL. '{{page}}' 페이지 템플릿 지원
  link_selector      TEXT,                  -- 목록에서 공지 링크(a) CSS
  content_selector   TEXT,                  -- 상세 본문 CSS
  url_prefix         TEXT NOT NULL DEFAULT '', -- 상대링크 접두(도메인 등)
  fetch_type         TEXT NOT NULL DEFAULT 'html',
                     -- 'html'(제네릭 CSS) | 'json_ssfilm' | 'json_mediamba'
                     -- | 'onclick_media' | 'post_lawyer' | 'dom_materials'
                     -- | 'login_*'(향후 ssupath 등, 현재 미사용)
  login              INTEGER NOT NULL DEFAULT 0,  -- 인증 필요 여부(향후). 0=공개
  seed_pages         INTEGER NOT NULL DEFAULT 3,  -- 최초 시딩 시 훑을 페이지 수
  discord_channel_id TEXT,                  -- 학과 전용 채널(없으면 NULL→자동생성 단계에서 채움)
  discord_role_id    TEXT,                  -- 학과 역할(구독=역할 보유)
  icon_url           TEXT,                  -- 임베드 footer 아이콘
  active             INTEGER NOT NULL DEFAULT 1,  -- 0=크롤 제외(예: infocom 문제 시 임시 off)
  seeded_at          TEXT,                  -- NULL=미시딩. 시딩 완료 시각 기록
  note               TEXT                   -- 운영 메모(예: '학교 서버 버그 재시도 대상')
);

-- ─────────────────────────────────────────────────────────────
-- 공지 = 차집합 "기억" + 처리 레코드 통합 (구 seen_notices + notices)
--   상태 흐름: seeded → detected → notified → summarizing → done | summary_failed | no_content
--     · seeded   = 목록에서 (제목·url)만 기억(무발송·무요약). 차집합의 "본 것".
--     · detected = 신규 감지 후 상세 fetch 완료.
--     · notified = 디스코드 D1(제목+링크) 발송 완료.
--     · done/…   = 요약 edit 완료 등.
--   차집합: [새 크롤 url] − [notices의 (dept_id,url)] = [신규]. UNIQUE(dept_id,url).
--   (제목까지 기억하므로 시딩 공지도 query 검색 가능. 내용/이미지는 처리 시에만 채움 → 자원 절약.)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notices (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id            TEXT NOT NULL REFERENCES depts(dept_id) ON DELETE CASCADE ON UPDATE CASCADE,
  title              TEXT NOT NULL,
  url                TEXT NOT NULL,
  content_raw        TEXT,                  -- 정제된 본문(처리 시). seeded면 NULL
  images_json        TEXT,                  -- '[{"url":...,"filename":...}]'
  ocr_text           TEXT,                  -- OCR 결과(있을 때)
  summary            TEXT,                  -- 요약 결과(있을 때)
  summary_engine     TEXT,                  -- 'Gemma-…' | 'vision:…' | NULL
  fail_reason        TEXT,                  -- 실패/내용없음 사유(사후 분석용). 성공 시 NULL
  status             TEXT NOT NULL DEFAULT 'seeded',
  discord_channel_id TEXT,                  -- 실제 발송된 채널
  discord_message_id TEXT,                  -- edit 대상 메시지
  first_seen_at      TEXT NOT NULL DEFAULT (datetime('now')),  -- 최초 기억(차집합)
  created_at         TEXT,                  -- 처리(promote) 시각
  updated_at         TEXT,
  UNIQUE(dept_id, url)                      -- 차집합 키(학과별). 같은 url을 두 학과가 공유 허용
);

CREATE INDEX IF NOT EXISTS idx_notices_status ON notices(status);   -- 부팅 시 미완 재적재
CREATE INDEX IF NOT EXISTS idx_notices_dept   ON notices(dept_id);
CREATE INDEX IF NOT EXISTS idx_notices_url    ON notices(url);      -- 차집합/검색

-- ─────────────────────────────────────────────────────────────
-- 구독 (원본 기록; 실제 노출 게이팅은 디스코드 역할)
--   발송은 채널 @everyone이라 발송 시 이 테이블 조회 불필요 → 사용자수와 자원 무관
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
  discord_user_id TEXT PRIMARY KEY,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
  discord_user_id TEXT NOT NULL REFERENCES users(discord_user_id) ON DELETE CASCADE,
  dept_id         TEXT NOT NULL REFERENCES depts(dept_id) ON DELETE CASCADE ON UPDATE CASCADE,
  subscribed_at   TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (discord_user_id, dept_id)
);

CREATE INDEX IF NOT EXISTS idx_subs_dept ON subscriptions(dept_id);

-- ─────────────────────────────────────────────────────────────
-- 앱 메타 (스키마 버전 등 키-값). 런타임 엔드포인트/모델은 config 파일에서 관리.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
INSERT OR IGNORE INTO app_meta(key, value) VALUES ('schema_version', '4');  -- v4: notices 단일 테이블(seen 통합)
