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
  dept_id            TEXT PRIMARY KEY,      -- 안정 슬러그. 예: 'cse', 'eco', 'portal_haksa'
  name_ko            TEXT NOT NULL,         -- 학과/카테고리 표시명
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
-- 차집합용 "기억" (sauron buffers/last-*.txt 대체)
--   [새 크롤 URL] − [여기 저장된 URL] = [신규]
--   URL 키라서 고정공지·순서꼬임을 자동 흡수 (BOLD 판별 불요)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS seen_notices (
  dept_id       TEXT NOT NULL REFERENCES depts(dept_id) ON DELETE CASCADE,
  url           TEXT NOT NULL,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (dept_id, url)
);

-- ─────────────────────────────────────────────────────────────
-- 공지 처리 큐 겸 아카이브
--   상태 흐름: detected → notified → summarizing → done | summary_failed
--   (알림/요약 분리: notified 시점에 이미 디스코드 발송 완료, message edit로 요약 삽입)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notices (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  dept_id            TEXT NOT NULL REFERENCES depts(dept_id) ON DELETE CASCADE,
  title              TEXT NOT NULL,
  url                TEXT NOT NULL UNIQUE,
  content_raw        TEXT,                  -- 정제된 본문 HTML/텍스트
  images_json        TEXT,                  -- '[{"url":...,"filename":...}]'
  ocr_text           TEXT,                  -- OCR 결과(있을 때)
  summary            TEXT,                  -- 요약 결과(있을 때)
  summary_engine     TEXT,                  -- 'gemma_e2b'|'gemma_e4b'|'clova'|NULL
  status             TEXT NOT NULL DEFAULT 'detected',
  discord_channel_id TEXT,                  -- 실제 발송된 채널
  discord_message_id TEXT,                  -- edit 대상 메시지
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_notices_status  ON notices(status);   -- 부팅 시 미완 재적재
CREATE INDEX IF NOT EXISTS idx_notices_dept    ON notices(dept_id);

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
  dept_id         TEXT NOT NULL REFERENCES depts(dept_id) ON DELETE CASCADE,
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
INSERT OR IGNORE INTO app_meta(key, value) VALUES ('schema_version', '1');
