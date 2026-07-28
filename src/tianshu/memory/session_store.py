"""会话持久化存储 — SQLite 方案。

每个会话存储完整的 messages 和 metadata，支持列表、恢复、删除。

Schema:
  sessions(
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    created_at REAL,
    updated_at REAL,
    messages_json TEXT,
    metadata_json TEXT
  )
"""

from __future__ import annotations

import json
import sqlite3
import time
import threading
from pathlib import Path
from typing import Any

from ..sdk.models import AgentContext


def _db_path() -> Path:
    """会话数据库路径。"""
    base = Path.home() / ".tianshu" / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    return base / "sessions.db"


class SessionStore:
    """会话持久化存储。"""

    def __init__(self, db_path: str = "") -> None:
        self._path = db_path or str(_db_path())
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            with sqlite3.connect(self._path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        title TEXT DEFAULT '',
                        created_at REAL,
                        updated_at REAL,
                        messages_json TEXT DEFAULT '[]',
                        metadata_json TEXT DEFAULT '{}'
                    )
                """)
                conn.commit()

    def save(self, ctx: AgentContext, title: str = "") -> None:
        """保存或更新会话。"""
        now = time.time()
        session_id = ctx.session_id

        messages_json = json.dumps(ctx.messages, ensure_ascii=False)
        metadata_json = json.dumps(ctx.metadata, ensure_ascii=False)

        with self._lock:
            with sqlite3.connect(self._path) as conn:
                # Check if exists
                row = conn.execute(
                    "SELECT created_at FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()

                if row:
                    conn.execute(
                        """UPDATE sessions
                           SET title = ?, updated_at = ?,
                               messages_json = ?, metadata_json = ?
                           WHERE id = ?""",
                        (title, now, messages_json, metadata_json, session_id),
                    )
                else:
                    conn.execute(
                        """INSERT INTO sessions
                           (id, title, created_at, updated_at, messages_json, metadata_json)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (session_id, title, now, now, messages_json, metadata_json),
                    )
                conn.commit()

    def load(self, session_id: str) -> AgentContext | None:
        """加载会话。"""
        with self._lock:
            with sqlite3.connect(self._path) as conn:
                row = conn.execute(
                    "SELECT messages_json, metadata_json FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()

        if row is None:
            return None

        try:
            messages = json.loads(row[0])
        except json.JSONDecodeError:
            messages = []

        try:
            metadata = json.loads(row[1])
        except json.JSONDecodeError:
            metadata = {}

        ctx = AgentContext(
            session_id=session_id,
            messages=messages,
            metadata=metadata,
        )
        return ctx

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近会话。"""
        with self._lock:
            with sqlite3.connect(self._path) as conn:
                rows = conn.execute(
                    """SELECT id, title, created_at, updated_at,
                              length(messages_json) as msg_size
                       FROM sessions
                       ORDER BY updated_at DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()

        result = []
        for row in rows:
            title = row[1] or ""
            if not title:
                # 从第一条用户消息提取（需要重新打开连接）
                try:
                    with sqlite3.connect(self._path) as conn2:
                        msg_row = conn2.execute(
                            "SELECT messages_json FROM sessions WHERE id = ?",
                            (row[0],),
                        ).fetchone()
                        if msg_row:
                            messages = json.loads(msg_row[0])
                            for m in messages:
                                if m.get("role") == "user":
                                    title = m.get("content", "")[:50]
                                    break
                except Exception:
                    pass

            result.append({
                "id": row[0],
                "title": title or "(empty)",
                "created_at": row[2],
                "updated_at": row[3],
            })

        return result

    def delete(self, session_id: str) -> bool:
        """删除会话。"""
        with self._lock:
            with sqlite3.connect(self._path) as conn:
                cursor = conn.execute(
                    "DELETE FROM sessions WHERE id = ?",
                    (session_id,),
                )
                conn.commit()
                return cursor.rowcount > 0

    def count(self) -> int:
        with self._lock:
            with sqlite3.connect(self._path) as conn:
                row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
                return row[0] if row else 0


# ═══════════════════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════════════════

_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
