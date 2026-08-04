"""投递账本 — Gateway 崩溃恢复，at-least-once 语义。

借鉴 Hermes delivery_ledger.py:
  每条待发消息写入 SQLite, 状态: pending → attempting → delivered | failed。
  崩溃重启后重发 pending/attempting 消息, 带 "可能重复" 标记。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

# 恢复标记——诚实告知可能重复
RECOVERED_MARKER = "♻️ [网关恢复] 以下回复可能在崩溃前已发送，若重复请忽略:\n\n"
MAX_ATTEMPTS = 3
STALE_AFTER_HOURS = 24


class DeliveryLedger:
    """投递账本——保证消息 at-least-once。"""

    def __init__(self, db_path: str | Path = ""):
        self._db_path = Path(db_path or "tianshu.db")

    async def _get_conn(self):
        return await aiosqlite.connect(str(self._db_path))

    async def init(self) -> None:
        """建表。"""
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS delivery_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT UNIQUE,
                    channel TEXT,
                    recipient TEXT,
                    content TEXT,
                    status TEXT DEFAULT 'pending',
                    attempts INTEGER DEFAULT 0,
                    created_at REAL,
                    updated_at REAL,
                    delivered_at REAL
                )"""
            )
            await db.commit()

    async def enqueue(
        self, message_id: str, channel: str, recipient: str, content: str,
    ) -> int:
        """入队: 消息发送前写入账本。"""
        async with aiosqlite.connect(str(self._db_path)) as db:
            now = time.time()
            cursor = await db.execute(
                """INSERT OR REPLACE INTO delivery_ledger
                   (message_id, channel, recipient, content, status, created_at, updated_at)
                   VALUES (?,?,?,?,'pending',?,?)""",
                (message_id, channel, recipient, content, now, now),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def mark_attempting(self, message_id: str) -> None:
        """标记为"正在发送"。"""
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "UPDATE delivery_ledger SET status='attempting', attempts=attempts+1, updated_at=? WHERE message_id=?",
                (time.time(), message_id),
            )
            await db.commit()

    async def mark_delivered(self, message_id: str) -> None:
        """标记为"已送达"。"""
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "UPDATE delivery_ledger SET status='delivered', delivered_at=?, updated_at=? WHERE message_id=?",
                (time.time(), time.time(), message_id),
            )
            await db.commit()

    async def mark_failed(self, message_id: str) -> None:
        """标记为"发送失败"。"""
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                "UPDATE delivery_ledger SET status='failed', updated_at=? WHERE message_id=?",
                (time.time(), message_id),
            )
            await db.commit()

    async def recover(self) -> list[dict[str, Any]]:
        """崩溃恢复: 返回所有待重发的消息。

        pending → 从未发送, 可以正常重发
        attempting → 可能已部分发送, 带 RECOVERED_MARKER
        """
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                """SELECT message_id, channel, recipient, content, status, attempts
                   FROM delivery_ledger
                   WHERE status IN ('pending','attempting')
                     AND attempts < ?
                     AND created_at > ?""",
                (MAX_ATTEMPTS, time.time() - STALE_AFTER_HOURS * 3600),
            )
            rows = await cursor.fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                mid, channel, recipient, content, status, attempts = row
                # attempting 状态标记可能重复
                if status == "attempting":
                    content = RECOVERED_MARKER + content
                results.append({
                    "message_id": mid,
                    "channel": channel,
                    "recipient": recipient,
                    "content": content,
                    "status": status,
                    "attempts": attempts,
                })
            return results

    async def cleanup(self) -> int:
        """清理超过保留期的旧记录。"""
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                "DELETE FROM delivery_ledger WHERE created_at < ? AND status != 'pending'",
                (time.time() - STALE_AFTER_HOURS * 3600 * 7,),  # 7 天保留
            )
            await db.commit()
            return cursor.rowcount
