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
        # Provider 接口——支持多后端扩展
        from .provider import SQLiteMemoryProvider
        self._provider = SQLiteMemoryProvider(base_dir=self._dir)

    @property
    def provider(self):
        """当前记忆后端。"""
        return self._provider

    def set_provider(self, provider) -> None:
        """替换记忆后端（ChromaDB / Honcho / Mem0 等）。"""
        self._provider = provider

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
        # 清理 FTS5 特殊字符
        safe_query = query
        for ch in '="*.-:()[]{}^~\\/\'><|&;!?,@#$%':
            safe_query = safe_query.replace(ch, ' ')
        safe_query = ' '.join(safe_query.split())  # 合并多余空格
        if not safe_query.strip():
            return []

        async with aiosqlite.connect(str(self._db_path)) as db:
            sql = (
                "SELECT key, value, category, rank, timestamp "
                "FROM memories WHERE memories MATCH ? "
            )
            params: list[Any] = [safe_query]
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

    # ── 跨会话记忆（Prefetch + Digest）─────────────────────────────

    async def prefetch(self, user_input: str, limit: int = 5) -> str:
        """对话前召回相关记忆，注入上下文。

        搜索 MEMORY.md + SQLite FTS5，返回与当前输入相关的历史记忆片段。
        """
        await self._init()
        results = await self.recall(user_input, limit=limit)
        if not results:
            return ""

        lines = ["[相关记忆]"]
        for r in results:
            lines.append(f"- [{r['category']}] {r['key']}: {r['value'][:200]}")
        return "\n".join(lines)

    async def digest(
        self,
        user_input: str,
        assistant_response: str,
        provider=None,
    ) -> list[str]:
        """对话后提取关键事实，保存到 MEMORY.md + SQLite。

        用 LLM 从对话中提取可复用的事实，存入长期记忆。
        返回提取到的事实列表。
        """
        await self._init()

        if not provider:
            return []

        prompt = (
            "Extract 1-3 reusable facts from this conversation. "
            "Each fact should be a single sentence that would be useful to remember.\n"
            "Return JSON array: [{\"key\":\"short_key\", \"value\":\"one sentence fact\", "
            "\"category\":\"preference|fact|goal\"}]\n\n"
            f"User: {user_input[:200]}\n"
            f"Assistant: {assistant_response[:300]}\n"
        )
        try:
            resp = await provider.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300, temperature=0.2,
            )
            text = resp.content or ""
            # 提取 JSON
            import re as _re
            match = _re.search(r'\[[\s\S]*\]', text)
            if match:
                items = json.loads(match.group())
                facts = []
                for item in items[:3]:
                    key = item.get("key", "")
                    value = item.get("value", "")
                    category = item.get("category", "fact")
                    if key and value:
                        await self.remember(key, value, category)
                        self.append_memory(f"- **{key}**: {value}")
                        facts.append(value)
                return facts
        except Exception:
            pass
        return []

    # ── 记忆衰减 ──────────────────────────────────────────────────

    async def decay(self, max_age_days: int = 30, min_importance: int = 1) -> int:
        """衰减旧记忆：删除超过 max_age_days 天且重要性 <= min_importance 的记忆。

        保留高重要性记忆（importance >= 3）和近期高频访问的记忆。
        返回删除的条数。
        """
        await self._init()
        cutoff = time.time() - max_age_days * 86400
        deleted = 0

        async with aiosqlite.connect(self._db_path) as db:
            # 删除旧且低优先级的记忆
            cursor = await db.execute(
                "DELETE FROM memory_meta WHERE created < ? AND importance <= ? "
                "AND access_count < 3",
                (cutoff, min_importance),
            )
            deleted = cursor.rowcount
            await db.commit()

            # 同步删除 FTS5 索引中的对应条目
            if deleted > 0:
                await db.execute("DELETE FROM memories WHERE timestamp < ?", (cutoff,))
                await db.commit()

        return deleted

    async def boost(self, key: str) -> None:
        """提升一条记忆的重要性——被召回时调用。"""
        await self._init()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE memory_meta SET access_count = access_count + 1, "
                "importance = MIN(5, importance + 1), updated = ? "
                "WHERE key = ?",
                (time.time(), key),
            )
            await db.commit()

    # ── 记忆压缩 ──────────────────────────────────────────────────

    async def compress(self, provider=None, max_chars: int = 3000) -> str | None:
        """压缩 MEMORY.md：超出 max_chars 时用 LLM 总结，保留关键事实。

        返回压缩后的文本，或 None（无需压缩）。
        """
        content = self.read_memory_file()
        if len(content) <= max_chars:
            return None

        if not provider:
            return None

        prompt = (
            "You are a memory compressor. Below is a long memory file. "
            "Condense it to under 2000 characters while preserving:\n"
            "1. User preferences (model choices, tools they like)\n"
            "2. Important facts (names, projects, deadlines)\n"
            "3. Recurring patterns (frequent topics, workflows)\n"
            "Remove: redundant facts, one-time queries, stale information.\n\n"
            f"MEMORY:\n{content[:5000]}\n\n"
            "Condensed memory (under 2000 chars):"
        )
        try:
            resp = await provider.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800, temperature=0.2,
            )
            compressed = (resp.content or "").strip()
            if compressed and len(compressed) > 100:
                # 备份原内容，写入压缩版
                backup_path = self._dir / "MEMORY.backup.md"
                backup_path.write_text(content, encoding="utf-8")
                self._mem_file.write_text(compressed, encoding="utf-8")
                return compressed
        except Exception:
            pass
        return None

    async def maybe_compress(self, provider=None) -> bool:
        """检查是否需要压缩，如果超出阈值则自动执行。"""
        await self.decay()  # 先衰减再压缩
        result = await self.compress(provider)
        return result is not None

    async def count(self) -> int:
        await self._init()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM memory_meta")
            row = await cursor.fetchone()
            return row[0] if row else 0
