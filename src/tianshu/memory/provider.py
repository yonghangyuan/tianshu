"""Memory Provider 抽象——支持多后端，完整生命周期。

借鉴 Hermes memory_provider.py (15 hooks) + OpenClaw 延迟加载。
当前: SQLiteMemoryProvider (默认, aiosqlite)
预留: ChromaDB, Honcho, Mem0
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseMemoryProvider(ABC):
    """记忆后端抽象——完整生命周期。

    核心 CRUD (必须实现):
      remember, recall, count, list_recent, delete, clear

    生命周期 hooks (可选——默认空操作):
      prefetch        — 每轮对话前查询相关记忆, 注入 system prompt
      sync_turn       — 每轮对话后同步 user+assistant 消息
      on_delegation   — 子 Agent 完成任务后观察结果
      on_pre_compress — 上下文压缩前最后一刻保存关键事实
      on_session_end  — 会话结束时清理/持久化
      system_prompt_block — 返回应注入 system prompt 的记忆块
    """

    # ── 核心 CRUD (必须实现) ──────────────────────────────────────

    @abstractmethod
    async def remember(self, key: str, value: str, category: str = "fact",
                       *, session_id: str = "") -> None: ...

    @abstractmethod
    async def recall(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def list_recent(self, limit: int = 10) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def delete(self, key: str) -> bool: ...

    @abstractmethod
    async def clear(self) -> None: ...

    # ── 生命周期 hooks (可选——默认空操作) ─────────────────────────

    async def prefetch(self, query: str, *, limit: int = 3) -> str:
        """每轮对话前查询相关记忆, 返回应注入 system prompt 的文本块。"""
        results = await self.recall(query, limit=limit)
        if not results:
            return ""
        return "\n".join(f"[{r['category']}] {r['key']}: {r['value'][:200]}" for r in results)

    async def sync_turn(self, user_input: str, assistant_output: str,
                        *, session_id: str = "") -> None:
        """每轮对话后同步。默认: 自动记住关键对话。"""
        if len(assistant_output) > 50:
            await self.remember(
                key=f"turn_{hash(user_input) % 100000}",
                value=f"Q: {user_input[:100]}\nA: {assistant_output[:200]}",
                category="conversation",
                session_id=session_id,
            )

    async def on_delegation(self, agent_name: str, task: str, result: str,
                            *, decision_id: str = "") -> None:
        """子 Agent 完成任务后调用。观察并记住 Agent 表现。"""
        await self.remember(
            key=f"delegation_{agent_name}_{decision_id[:8] if decision_id else 'x'}",
            value=f"Agent: {agent_name}\nTask: {task[:100]}\nResult: {result[:200]}",
            category="delegation",
        )

    async def on_pre_compress(self, messages_to_compress: list[dict],
                               *, session_id: str = "") -> None:
        """压缩前保存即将被丢弃的中间消息到记忆。"""
        for m in messages_to_compress[:5]:  # 最多保存 5 条
            content = str(m.get("content", ""))[:300]
            if content.strip():
                await self.remember(
                    key=f"compressed_{hash(content) % 100000}",
                    value=content,
                    category="compressed_context",
                    session_id=session_id,
                )

    async def on_session_end(self, *, session_id: str = "") -> None:
        """会话结束清理。默认: 无操作。"""
        pass

    async def system_prompt_block(self) -> str:
        """返回应注入 system prompt 的记忆块。默认: 最近 5 条事实。"""
        recent = await self.list_recent(5)
        if not recent:
            return ""
        return "## Memory\n" + "\n".join(
            f"- [{r['category']}] {r['key']}: {r['value'][:150]}"
            for r in recent
        )


class SQLiteMemoryProvider(BaseMemoryProvider):
    """默认 SQLite 后端。"""

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

    async def export_markdown(self) -> str:
        """导出为 MEMORY.md 格式 (#16)。"""
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT key, value, category, created_at FROM memory ORDER BY category, created_at DESC"
        )
        rows = await cursor.fetchall()
        lines = ["# Tianshu Memory Export\n"]
        current_cat = ""
        for key, value, category, ts in rows:
            if category != current_cat:
                current_cat = category
                lines.append(f"\n## {category}\n")
            lines.append(f"- **{key}**: {value[:200]}")
        return "\n".join(lines)
