"""Skill Service — Skills 发现/加载/执行/观测的统一封装。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .loader import SkillLoader
from .executor import SkillExecutor
from .observer import SkillObserver


class SkillService:
    """Skill 服务——统一 Skills 生命周期管理。"""

    def __init__(self) -> None:
        self._loader = SkillLoader()
        self._executor = SkillExecutor(self._loader)
        self._observer = SkillObserver()
        self._loaded = False

    # ── 初始化 ─────────────────────────────────────────────────────

    def discover_and_load(self) -> None:
        """扫描 + 加载内置 + 加载用户 Skills。"""
        self._loader.discover()
        self._loader.load_builtins()
        self._loader.load_user_skills()
        self._executor.register_all()
        self._loaded = True

    # ── 工具 ───────────────────────────────────────────────────────

    def get_tools(self) -> list[dict[str, Any]]:
        """获取所有 Skill 工具的 OpenAI schema。"""
        return self._loader.get_all_tools()

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """执行一个 Skill 工具。"""
        return await self._executor.execute(name, arguments)

    # ── 观测 ─────────────────────────────────────────────────────

    def observe_turn(self, task_description: str) -> None:
        self._observer.finish_turn(task_description)

    def check_evolution(self) -> list[dict[str, Any]]:
        """检测是否有达到阈值的模式。"""
        return self._observer.check_evolution()

    def build_skill_prompt(self, pattern: dict) -> str:
        """生成 SKILL.md 的 LLM 提示词。"""
        return self._observer.build_skill_prompt(pattern)

    def mark_skill_processed(self, signature: str) -> None:
        """标记模式已处理。"""
        self._observer.mark_processed(signature)

    async def evolve_skills(self, provider) -> list[str]:
        """检测 → 调 LLM 生成 → 保存 SKILL.md。

        Args:
            provider: 用于生成 SKILL.md 的 LLM provider（用便宜的）

        Returns:
            新生成的 Skill 名列表。
        """
        patterns = self.check_evolution()
        if not patterns:
            return []

        new_skills = []
        for p in patterns:
            prompt = self.build_skill_prompt(p)
            try:
                resp = await provider.chat(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=500,
                )
                skill_md = resp.content or ""
                if "---" not in skill_md:
                    continue

                # 保存到 ~/.tianshu/skills/
                skill_dir = Path.home() / ".tianshu" / "skills"
                skill_dir.mkdir(parents=True, exist_ok=True)
                skill_file = skill_dir / f"{p['name']}.md"
                skill_file.write_text(skill_md, encoding="utf-8")

                # 标记已处理 + 重新加载
                self.mark_skill_processed(p["signature"])
                new_skills.append(p["name"])
            except Exception:
                pass

        # 重新加载 Skills
        if new_skills:
            self._loader.discover()
            self._loader.load_user_skills()
            self._executor.register_all()

        return new_skills

    # ── 状态 ─────────────────────────────────────────────────────

    @property
    def loader(self) -> SkillLoader:
        return self._loader

    @property
    def observer(self) -> SkillObserver:
        return self._observer

    @property
    def count(self) -> int:
        return self._loader.skill_count

    @property
    def tool_count(self) -> int:
        return len(self._loader.get_all_tools())

    def list_skills(self) -> list[dict[str, Any]]:
        return self._loader.list_skills()
