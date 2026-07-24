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

    # ── seen_notices (차집합) ──────────────────────────
    def seen_urls(self, dept_id: str) -> Set[str]:
        with self._lock:
            rows = self._con.execute(
                "SELECT url FROM seen_notices WHERE dept_id=?", (dept_id,)).fetchall()
        return {r["url"] for r in rows}

    def mark_seen(self, dept_id: str, urls):
        urls = list(urls)
        if not urls:
            return
        with self._lock:
            self._con.executemany(
                "INSERT OR IGNORE INTO seen_notices(dept_id, url) VALUES (?, ?)",
                [(dept_id, u) for u in urls],
            )
            self._con.commit()

    # ── notices (큐 겸 아카이브) ───────────────────────
    def insert_notice(self, dept_id: str, title: str, url: str,
                      content_raw: str = None, images_json: str = None) -> Optional[int]:
        """신규 공지 삽입. url UNIQUE 충돌 시 None(이미 존재)."""
        with self._lock:
            try:
                cur = self._con.execute(
                    "INSERT INTO notices(dept_id, title, url, content_raw, images_json, status) "
                    "VALUES (?,?,?,?,?, 'detected')",
                    (dept_id, title, url, content_raw, images_json),
                )
                self._con.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

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
                    ocr_text: str = None, status: str = "done"):
        with self._lock:
            self._con.execute(
                "UPDATE notices SET summary=?, summary_engine=?, ocr_text=COALESCE(?, ocr_text), "
                "status=?, updated_at=datetime('now') WHERE id=?",
                (summary, engine, ocr_text, status, notice_id))
            self._con.commit()

    def update_content(self, notice_id: int, content_raw: str, images_json: str):
        with self._lock:
            self._con.execute(
                "UPDATE notices SET content_raw=?, images_json=?, updated_at=datetime('now') WHERE id=?",
                (content_raw, images_json, notice_id))
            self._con.commit()

    def forget_url(self, dept_id: str, url: str):
        """디버그용: seen에서 지우고 notices의 기존 행도 제거 → 다음 크롤에 '신규'로 재감지·재요약."""
        with self._lock:
            self._con.execute("DELETE FROM seen_notices WHERE dept_id=? AND url=?", (dept_id, url))
            self._con.execute("DELETE FROM notices WHERE url=?", (url,))
            self._con.commit()

    def pending_summary_ids(self) -> List[int]:
        """부팅 재적재: 아직 요약 안 끝난(발송됐거나 요약중) 공지."""
        with self._lock:
            rows = self._con.execute(
                "SELECT id FROM notices WHERE status IN ('detected','notified','summarizing') ORDER BY id"
            ).fetchall()
        return [r["id"] for r in rows]

    def recent_notices(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM notices ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

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
