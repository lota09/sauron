# -*- coding: utf-8 -*-
"""
db/store.py — SQLite 접근 계층.
단일 프로세스/멀티스레드(asyncio.to_thread) 안전: check_same_thread=False + RLock.
"""
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Set


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL;")
        self._con.execute("PRAGMA foreign_keys=ON;")
        self._lock = threading.RLock()

    def checkpoint(self):
        """WAL을 본 DB 파일로 flush(TRUNCATE). 외부 뷰어가 최신 상태를 보게 함."""
        with self._lock:
            try:
                self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass

    def close(self):
        with self._lock:
            self.checkpoint()
            self._con.close()

    # ── depts ──────────────────────────────────────────
    def active_depts(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM depts WHERE active=1 ORDER BY dept_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def depts_by_kind(self, kind: str, with_role: bool = False) -> List[Dict[str, Any]]:
        """active 학과를 kind('general'|'major'|'etc')로 필터. with_role=True면 역할 배정된 것만."""
        sql = "SELECT * FROM depts WHERE active=1 AND kind=?"
        if with_role:
            sql += " AND discord_role_id IS NOT NULL AND discord_role_id<>''"
        sql += " ORDER BY college, name_ko"
        with self._lock:
            rows = self._con.execute(sql, (kind,)).fetchall()
        return [dict(r) for r in rows]

    def get_dept(self, dept_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            r = self._con.execute("SELECT * FROM depts WHERE dept_id=?", (dept_id,)).fetchone()
        return dict(r) if r else None

    def is_seeded(self, dept_id: str) -> bool:
        with self._lock:
            r = self._con.execute("SELECT seeded_at FROM depts WHERE dept_id=?", (dept_id,)).fetchone()
        return bool(r and r["seeded_at"])

    def set_seeded(self, dept_id: str):
        with self._lock:
            self._con.execute(
                "UPDATE depts SET seeded_at=datetime('now') WHERE dept_id=?", (dept_id,))
            self._con.commit()

    # ── 차집합 "기억" = notices의 (dept_id,url) ───────────
    def seen_urls(self, dept_id: str) -> Set[str]:
        """이 학과에서 이미 본 url 집합(status 무관: seeded 포함)."""
        with self._lock:
            rows = self._con.execute(
                "SELECT url FROM notices WHERE dept_id=?", (dept_id,)).fetchall()
        return {r["url"] for r in rows}

    def seed_rows(self, dept_id: str, items):
        """목록 항목(제목+url)을 status='seeded'로 기억(무발송·무요약). items=[{'title','url'}]."""
        items = list(items)
        if not items:
            return
        with self._lock:
            self._con.executemany(
                "INSERT OR IGNORE INTO notices(dept_id, url, title, status) VALUES (?,?,?, 'seeded')",
                [(dept_id, it["url"], (it.get("title") or "(제목없음)")) for it in items],
            )
            self._con.commit()

    # ── notices (처리 레코드) ──────────────────────────
    def promote_notice(self, dept_id: str, title: str, url: str,
                       content_raw: str = None, images_json: str = None) -> Optional[int]:
        """seeded(또는 미존재) 행을 detected로 승격하며 내용 채움(upsert). 반환 notice_id.
        기존 done 행 재처리 시에도 summary/engine/fail_reason 초기화 후 detected로 되돌림."""
        with self._lock:
            self._con.execute(
                "INSERT INTO notices(dept_id, title, url, content_raw, images_json, status, created_at) "
                "VALUES (?,?,?,?,?, 'detected', datetime('now')) "
                "ON CONFLICT(dept_id, url) DO UPDATE SET "
                "  title=excluded.title, content_raw=excluded.content_raw, images_json=excluded.images_json, "
                "  status='detected', created_at=datetime('now'), "
                "  summary=NULL, summary_engine=NULL, fail_reason=NULL",
                (dept_id, title, url, content_raw, images_json))
            r = self._con.execute(
                "SELECT id FROM notices WHERE dept_id=? AND url=?", (dept_id, url)).fetchone()
            self._con.commit()
        return r["id"] if r else None

    def get_notice(self, notice_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            r = self._con.execute("SELECT * FROM notices WHERE id=?", (notice_id,)).fetchone()
        return dict(r) if r else None

    def set_notified(self, notice_id: int, channel_id: str, message_id: str):
        with self._lock:
            self._con.execute(
                "UPDATE notices SET status='notified', discord_channel_id=?, "
                "discord_message_id=?, updated_at=datetime('now') WHERE id=?",
                (channel_id, message_id, notice_id))
            self._con.commit()

    def set_status(self, notice_id: int, status: str):
        with self._lock:
            self._con.execute(
                "UPDATE notices SET status=?, updated_at=datetime('now') WHERE id=?",
                (status, notice_id))
            self._con.commit()

    def set_summary(self, notice_id: int, summary: str, engine: str,
                    ocr_text: str = None, status: str = "done", fail_reason: str = None):
        with self._lock:
            self._con.execute(
                "UPDATE notices SET summary=?, summary_engine=?, ocr_text=COALESCE(?, ocr_text), "
                "status=?, fail_reason=?, updated_at=datetime('now') WHERE id=?",
                (summary, engine, ocr_text, status, fail_reason, notice_id))
            self._con.commit()

    def update_content(self, notice_id: int, content_raw: str, images_json: str):
        with self._lock:
            self._con.execute(
                "UPDATE notices SET content_raw=?, images_json=?, updated_at=datetime('now') WHERE id=?",
                (content_raw, images_json, notice_id))
            self._con.commit()

    def forget_url(self, dept_id: str, url: str):
        """디버그용: notices에서 행 제거 → 다음 크롤에 '신규'로 재감지·재처리."""
        with self._lock:
            self._con.execute("DELETE FROM notices WHERE dept_id=? AND url=?", (dept_id, url))
            self._con.commit()

    def forget_like(self, url_substring: str) -> int:
        """url에 부분문자열이 포함된 공지를 notices에서 삭제 → 다음 크롤에 재감지·재처리.
        instr 사용(LIKE의 % 와일드카드와 URL의 % 인코딩 충돌 회피). 반환: 지운 행 수."""
        with self._lock:
            cur = self._con.execute("DELETE FROM notices WHERE instr(url, ?) > 0", (url_substring,))
            n = cur.rowcount
            self._con.commit()
        return n

    def delete_notice(self, notice_id: int) -> None:
        """query 모드 'DB에서 제거' 용: 특정 공지 행 삭제 → 재감지 대상이 됨."""
        with self._lock:
            self._con.execute("DELETE FROM notices WHERE id=?", (notice_id,))
            self._con.commit()

    def pending_summary_ids(self) -> List[int]:
        """부팅 재적재: 아직 요약 안 끝난(발송됐거나 요약중) 공지."""
        with self._lock:
            rows = self._con.execute(
                "SELECT id FROM notices WHERE status IN ('detected','notified','summarizing') ORDER BY id"
            ).fetchall()
        return [r["id"] for r in rows]

    def search_notices(self, query: str, limit: int = 30) -> List[Dict[str, Any]]:
        """제목에 query 부분문자열이 든 공지 검색(최신순). redo 검색용."""
        with self._lock:
            rows = self._con.execute(
                "SELECT id, dept_id, title, url, status FROM notices WHERE instr(title, ?)>0 "
                "ORDER BY id DESC LIMIT ?", (query, limit)).fetchall()
        return [dict(r) for r in rows]

    def recent_notices(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM notices ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ── app_meta (키-값: schema_version, 자동생성 채널ID 등) ──
    def get_meta(self, key: str, default=None):
        with self._lock:
            r = self._con.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO app_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
            self._con.commit()

    # ── 구독 (후속 단계에서 봇이 사용) ─────────────────
    def add_user(self, discord_user_id: str):
        with self._lock:
            self._con.execute(
                "INSERT OR IGNORE INTO users(discord_user_id) VALUES (?)", (discord_user_id,))
            self._con.commit()

    def set_subscriptions(self, discord_user_id: str, dept_ids: List[str]):
        with self._lock:
            self.add_user(discord_user_id)
            self._con.execute("DELETE FROM subscriptions WHERE discord_user_id=?", (discord_user_id,))
            self._con.executemany(
                "INSERT OR IGNORE INTO subscriptions(discord_user_id, dept_id) VALUES (?,?)",
                [(discord_user_id, d) for d in dept_ids])
            self._con.commit()

    def user_subscriptions(self, discord_user_id: str) -> List[str]:
        with self._lock:
            rows = self._con.execute(
                "SELECT dept_id FROM subscriptions WHERE discord_user_id=?", (discord_user_id,)).fetchall()
        return [r["dept_id"] for r in rows]

    def add_subscription(self, discord_user_id: str, dept_id: str):
        with self._lock:
            self.add_user(discord_user_id)
            self._con.execute(
                "INSERT OR IGNORE INTO subscriptions(discord_user_id, dept_id) VALUES (?,?)",
                (discord_user_id, dept_id))
            self._con.commit()

    def remove_subscription(self, discord_user_id: str, dept_id: str):
        with self._lock:
            self._con.execute(
                "DELETE FROM subscriptions WHERE discord_user_id=? AND dept_id=?",
                (discord_user_id, dept_id))
            self._con.commit()

    def set_dept_discord(self, dept_id: str, channel_id: str = None, role_id: str = None):
        """setup_guild가 생성한 채널/역할 ID를 depts에 기록."""
        with self._lock:
            if channel_id is not None:
                self._con.execute("UPDATE depts SET discord_channel_id=? WHERE dept_id=?", (channel_id, dept_id))
            if role_id is not None:
                self._con.execute("UPDATE depts SET discord_role_id=? WHERE dept_id=?", (role_id, dept_id))
            self._con.commit()
