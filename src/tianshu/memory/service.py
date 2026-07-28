"""Memory Service — 两层记忆系统。

借鉴 Hermes 五层记忆，MVP 实现 L2 + L5：
  L2: 持久记忆文件 — MEMORY.md (事实) + USER.md (偏好)
  L5: SQLite FTS5 — 跨会话全文检索 + 语义摘要
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

# ── Schema ────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(
    key,
    value,
    category,
    session_id,
    timestamp,
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS memory_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    importance INT DEFAULT 1,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    access_count INT DEFAULT 0
);
"""


class MemoryService:
    """记忆服务 — Agent 的长期记忆。

    用法:
        mem = MemoryService()
        await mem.remember("user_prefers_v4_pro", "deepseek/v4-pro", "preference")
        results = await mem.recall("model preference", limit=5)
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        if base_dir is None:
            base_dir = Path.home() / ".tianshu" / "memory"
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._dir / "memories.db"
        self._mem_file = self._dir / "MEMORY.md"
        self._user_file = self._dir / "USER.md"
        self._initialized = False

    async def _init(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        self._initialized = True

    # ── L5: FTS5 写入/检索 ──────────────────────────────────────────

    async def remember(
        self, key: str, value: str,
        category: str = "general",
        importance: int = 1,
        session_id: str = "",
    ) -> None:
        """存储一条记忆。自动去重——同 key 则更新。"""
        await self._init()
        now = time.time()
        async with aiosqlite.connect(str(self._db_path)) as db:
            # Upsert meta
            await db.execute(
                """INSERT OR REPLACE INTO memory_meta
                   (key, value, category, importance, created, updated, access_count)
                   VALUES (?, ?, ?, ?,
                     COALESCE((SELECT created FROM memory_meta WHERE key=?), ?),
                     ?, COALESCE((SELECT access_count FROM memory_meta WHERE key=?), 0))""",
                (key, value[:2000], category, importance, key, now, now, key),
            )
            # FTS5 index
            await db.execute(
                "INSERT INTO memories (key, value, category, session_id, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, value[:2000], category, session_id, now),
            )
            await db.commit()

    async def recall(
        self, query: str, limit: int = 5, category: str | None = None
    ) -> list[dict[str, Any]]:
        """全文检索记忆。"""
        await self._init()
        async with aiosqlite.connect(str(self._db_path)) as db:
            sql = (
                "SELECT key, value, category, rank, timestamp "
                "FROM memories WHERE memories MATCH ? "
            )
            params: list[Any] = [query]
            if category:
                sql += " AND category = ?"
                params.append(category)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)

            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [
                {"key": r[0], "value": r[1], "category": r[2],
                 "rank": r[3], "timestamp": r[4]}
                for r in rows
            ]

    async def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近存储的记忆。"""
        await self._init()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                "SELECT key, value, category, importance, updated, access_count "
                "FROM memory_meta ORDER BY updated DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                {"key": r[0], "value": r[1], "category": r[2],
                 "importance": r[3], "updated": r[4], "access_count": r[5]}
                for r in rows
            ]

    # ── L2: 持久记忆文件 ────────────────────────────────────────────

    def read_memory_file(self) -> str:
        """读取 MEMORY.md。"""
        if self._mem_file.exists():
            return self._mem_file.read_text(encoding="utf-8")
        return ""

    def append_memory(self, fact: str) -> None:
        """追加一条事实到 MEMORY.md。"""
        content = self.read_memory_file()
        entry = f"- {fact}  ({time.strftime('%Y-%m-%d')})\n"
        if len(content) + len(entry) > 3000:
            # 满了：LLM 应该压缩，但这里先截断
            content = content[-2000:]
        self._mem_file.write_text(content + entry, encoding="utf-8")

    def read_user_profile(self) -> str:
        """读取 USER.md。"""
        if self._user_file.exists():
            return self._user_file.read_text(encoding="utf-8")
        return ""

    def update_user_profile(self, key: str, value: str) -> None:
        """更新用户画像：偏好/习惯。"""
        profile = self.read_user_profile()
        lines = profile.split("\n")
        new_lines = []
        found = False
        for line in lines:
            if line.startswith(f"- **{key}**:"):
                new_lines.append(f"- **{key}**: {value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"- **{key}**: {value}")
        self._user_file.write_text("\n".join(new_lines), encoding="utf-8")

    # ── L4: 自动用户画像（Honcho 式） ───────────────────────────────

    async def auto_profile(
        self, user_input: str, assistant_response: str, provider
    ) -> list[dict[str, str]]:
        """从对话中自动提取用户特征——偏好、事实、目标。

        借鉴 Hermes Honcho 的 conclude 机制：
        不存原文，存结论。

        Args:
            user_input: 用户说的话
            assistant_response: Agent 的回复
            provider: LLM provider（用便宜的做提取）

        Returns:
            提取到的 [{key, value, category}, ...]
        """
        prompt = (
            "Extract facts about the user from this conversation. "
            "Return ONLY a JSON array. No explanation.\n\n"
            "Format: [{\"key\": \"...\", \"value\": \"...\", \"category\": \"preference|fact|goal\"}]\n\n"
            "Categories:\n"
            "  preference = what the user likes/dislikes (model choice, tools, style)\n"
            "  fact = objective information (name, role, location, project)\n"
            "  goal = what the user is trying to achieve (deadline, project, task)\n\n"
            "Extract only NEW information not already obvious. Return [] if nothing new.\n\n"
            f"User: {user_input[:300]}\n"
            f"Assistant: {assistant_response[:200]}"
        )
        try:
            resp = await provider.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.1,
            )
            import json
            text = (resp.content or "").strip()
            # 清理 markdown code block
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
            items = json.loads(text)
            if not isinstance(items, list):
                return []

            results = []
            for item in items[:5]:  # 最多 5 条
                key = item.get("key", "")
                value = item.get("value", "")
                cat = item.get("category", "fact")
                if key and value:
                    await self.remember(key, value, cat)
                    results.append({"key": key, "value": value, "category": cat})

            # 更新 USER.md
            if results:
                self._update_user_md(results)

            return results
        except Exception:
            return []

    def _update_user_md(self, items: list[dict]) -> None:
        """更新 USER.md 用户画像文件。"""
        profile = self.read_user_profile()
        new_sections: dict[str, list[str]] = {"preference": [], "fact": [], "goal": []}
        for item in items:
            new_sections[item["category"]].append(
                f"- **{item['key']}**: {item['value']}"
            )

        if not profile:
            profile = "# User Profile\n\n"
        for cat, entries in new_sections.items():
            if entries and f"## {cat}" not in profile:
                profile += f"\n## {cat.capitalize()}\n"
            for entry in entries:
                if entry not in profile:
                    profile += entry + "\n"

        self._user_file.write_text(profile, encoding="utf-8")

    # ── 统计 ─────────────────────────────────────────────────────────

    async def count(self) -> int:
        await self._init()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM memory_meta")
            row = await cursor.fetchone()
            return row[0] if row else 0
