"""Memory Provider 抽象——支持多后端。

当前: SQLiteMemoryProvider (默认, aiosqlite)
预留: ChromaDB, Honcho, Mem0
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseMemoryProvider(ABC):
    """记忆后端抽象——所有 Memory provider 实现此接口。

    方法语义与 MemoryService 一致，确保无缝替换。
    """

    @abstractmethod
    async def remember(
        self, key: str, value: str, category: str = "fact",
        *, session_id: str = "",
    ) -> None:
        """存一条记忆。"""

    @abstractmethod
    async def recall(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """关键词/语义搜索。返回 [{key, value, category, score}]。"""

    @abstractmethod
    async def count(self) -> int:
        """总记忆数。"""

    @abstractmethod
    async def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """最近 N 条。"""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """删除一条记忆。"""

    @abstractmethod
    async def clear(self) -> None:
        """清空所有记忆。"""


class SQLiteMemoryProvider(BaseMemoryProvider):
    """默认 SQLite 后端——与现有 MemoryService 行为一致。"""

    def __init__(self, base_dir: str | Path = ""):
        import aiosqlite
        self._db_path = Path(base_dir or Path.home() / ".tianshu" / "memory") / "memory.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Any = None

    async def _get_conn(self):
        if self._conn is None:
            import aiosqlite
            self._conn = await aiosqlite.connect(str(self._db_path))
            await self._conn.execute(
                """CREATE TABLE IF NOT EXISTS memory (
                    key TEXT PRIMARY KEY, value TEXT, category TEXT,
                    session_id TEXT, created_at REAL, access_count INTEGER DEFAULT 0)"""
            )
            await self._conn.commit()
        return self._conn

    async def remember(self, key, value, category="fact", *, session_id=""):
        import time
        conn = await self._get_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO memory VALUES (?,?,?,?,?, COALESCE((SELECT access_count FROM memory WHERE key=?),0))",
            (key, value, category, session_id, time.time(), key),
        )
        await conn.commit()

    async def recall(self, query, *, limit=5):
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT key, value, category FROM memory WHERE value LIKE ? OR key LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        )
        rows = await cursor.fetchall()
        return [{"key": r[0], "value": r[1], "category": r[2]} for r in rows]

    async def count(self):
        conn = await self._get_conn()
        cursor = await conn.execute("SELECT COUNT(*) FROM memory")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def list_recent(self, limit=10):
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT key, value, category, created_at FROM memory ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [{"key": r[0], "value": r[1], "category": r[2]} for r in rows]

    async def delete(self, key):
        conn = await self._get_conn()
        cursor = await conn.execute("DELETE FROM memory WHERE key=?", (key,))
        await conn.commit()
        return cursor.rowcount > 0

    async def clear(self):
        conn = await self._get_conn()
        await conn.execute("DELETE FROM memory")
        await conn.commit()
