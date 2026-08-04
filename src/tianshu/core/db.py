"""统一数据库管理——DDIA 指导下的轻量方案。

原则:
  - 一个文件管所有表定义 + 初始化 + 迁移
  - aiosqlite + 原始 SQL，不引入 ORM
  - 日志结构（append-only）用于消息/事件
  - WAL 模式用于并发读
  - Schema 版本号管理

用法:
    db = Database("tianshu.db")
    await db.init()
    async with db.connect() as conn:
        await conn.execute("...")
"""

from __future__ import annotations

import aiosqlite
from pathlib import Path

SCHEMA_VERSION = 2


class Database:
    """天枢统一数据库。"""

    def __init__(self, path: str = "tianshu.db"):
        self._path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def path(self) -> str:
        return str(self._path)

    async def init(self) -> None:
        """初始化所有表。幂等——重复调用安全。"""
        self._conn = await aiosqlite.connect(str(self._path))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        await self._create_all()
        await self._migrate()
        await self._conn.commit()

    async def connect(self):
        """获取连接。init() 后再调用。"""
        if not self._conn:
            await self.init()
        return self._conn

    async def _create_all(self) -> None:
        """建所有表（IF NOT EXISTS，幂等）。"""
        conn = self._conn

        # ── Schema 版本 ──
        await conn.execute("""CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY, applied_at REAL)""")

        # ── 记忆 ──
        await conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(
            key, value, category, session_id, timestamp,
            tokenize='porter unicode61')""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS memory_meta (
            key TEXT PRIMARY KEY, value TEXT, category TEXT DEFAULT 'general',
            importance INT DEFAULT 1, created REAL, updated REAL,
            access_count INT DEFAULT 0)""")

        # ── 会话 ──
        await conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, title TEXT DEFAULT '',
            created_at REAL, updated_at REAL,
            messages_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}')""")

        # ── 审计 ──
        await conn.execute("""CREATE TABLE IF NOT EXISTS audit_records (
            decision_id TEXT PRIMARY KEY, timestamp REAL,
            llm_model TEXT, llm_config TEXT, system_prompt_version TEXT,
            available_tools TEXT, input_count INT, input_data TEXT,
            reasoning_chain TEXT, output_commands TEXT,
            evaluation TEXT, level INT, session_id TEXT, task_type TEXT)""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS causal_chain (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id TEXT, child_id TEXT, relation TEXT, timestamp REAL)""")

        # ── 聊天 ──
        await conn.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY, sender TEXT, content TEXT, time REAL)""")

        # ── 任务空间 ──
        await conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY, title TEXT, description TEXT,
            status TEXT DEFAULT 'todo', created_by TEXT,
            created_at REAL, updated_at REAL)""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS task_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER,
            sender TEXT, content TEXT, time REAL)""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS task_agents (
            name TEXT, task_id INTEGER, system_prompt TEXT,
            model TEXT, created_by TEXT, created_at REAL,
            PRIMARY KEY(task_id, name))""")

        # ── 群聊 Agent（星群）── 持久化
        await conn.execute("""CREATE TABLE IF NOT EXISTS chat_agents (
            name TEXT PRIMARY KEY, system_prompt TEXT DEFAULT '',
            model TEXT DEFAULT '', created_by TEXT DEFAULT '',
            created_at REAL)""")

        # ── 审计快照（轻量级，用于压缩记录等）──
        await conn.execute("""CREATE TABLE IF NOT EXISTS audit_snapshots (
            decision_id TEXT PRIMARY KEY, data TEXT, created_at REAL)""")

    async def _migrate(self) -> None:
        """Schema 迁移。"""
        conn = self._conn
        c = await conn.execute("SELECT MAX(version) FROM schema_version")
        row = await c.fetchone()
        current = row[0] if row[0] else 0

        if current < 1:
            await conn.execute(
                "INSERT INTO schema_version VALUES(1, unixepoch())")
            current = 1

        if current < 2:
            # v2: 添加 chat_agents 表
            await conn.execute("""CREATE TABLE IF NOT EXISTS chat_agents (
                name TEXT PRIMARY KEY, system_prompt TEXT DEFAULT '',
                model TEXT DEFAULT '', created_by TEXT DEFAULT '',
                created_at REAL)""")
            await conn.execute(
                "INSERT INTO schema_version VALUES(2, unixepoch())")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()


# ── 全局单例 ───────────────────────────────────────────────

_db: Database | None = None


def get_db(path: str = "tianshu.db") -> Database:
    global _db
    if _db is None:
        _db = Database(path)
    return _db
