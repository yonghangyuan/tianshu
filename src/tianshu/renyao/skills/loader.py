"""Skill 加载器 — 发现、加载、匹配 Skills。

借鉴 Hermes:
  - 内置 Skills: src/tianshu/renyao/skills/（Python 类）
  - 用户 Skills: ~/.tianshu/skills/（SKILL.md）
  - 自进化 Skills 也在用户目录
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from .base import BaseSkill, SkillDef, UserSkill


def _user_skills_dir() -> Path:
    return Path.home() / ".tianshu" / "skills"


def _builtin_skills_dir() -> Path:
    return Path(__file__).resolve().parent


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 YAML frontmatter + Markdown body。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()


class SkillLoader:
    """Skills 发现、加载、匹配。

    用法:
        loader = SkillLoader()
        loader.discover()          # 扫描所有目录
        loader.load_builtins()     # 加载内置 Python Skills
        tools = loader.get_all_tools()  # 获取 OpenAI schema
    """

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}     # name → Skill 实例
        self._defs: dict[str, SkillDef] = {}         # name → SkillDef
        self._user_docs: dict[str, str] = {}          # name → body (用户 Skill)

    # ── 发现 ─────────────────────────────────────────────────────────

    def discover(self) -> list[SkillDef]:
        """扫描所有 Skills 目录，解析 SKILL.md。

        Returns:
            SkillDef 列表，按 usage_count 降序排列（常用的排前面）。
        """
        defs: list[SkillDef] = []

        # 1. 用户 Skills 目录
        user_dir = _user_skills_dir()
        if user_dir.exists():
            for md_file in sorted(user_dir.glob("*.md")):
                try:
                    text = md_file.read_text(encoding="utf-8")
                    meta, body = _parse_frontmatter(text)
                    if meta.get("name"):
                        sd = SkillDef(
                            name=meta["name"],
                            description=meta.get("description", ""),
                            trigram=meta.get("trigram", "人"),
                            tool_names=meta.get("tools", []),
                            trigger_keywords=meta.get("trigger_keywords", []),
                            version=meta.get("version", 1),
                            usage_count=meta.get("usage_count", 0),
                            created=meta.get("created", "manual"),
                            source_path=str(md_file),
                        )
                        defs.append(sd)
                        self._defs[sd.name] = sd
                        self._user_docs[sd.name] = body
                except Exception:
                    pass

        # 2. 内置 Skills 目录
        builtin_dir = _builtin_skills_dir()
        for md_file in sorted(builtin_dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(text)
                if meta.get("name") and meta.get("name") not in self._defs:
                    sd = SkillDef(
                        name=meta["name"],
                        description=meta.get("description", ""),
                        trigram=meta.get("trigram", "人"),
                        tool_names=meta.get("tools", []),
                        trigger_keywords=meta.get("trigger_keywords", []),
                        version=meta.get("version", 1),
                        usage_count=meta.get("usage_count", 0),
                        created="manual",
                        source_path=str(md_file),
                    )
                    defs.append(sd)
                    self._defs[sd.name] = sd
            except Exception:
                pass

        # 按 usage_count 降序
        defs.sort(key=lambda d: d.usage_count, reverse=True)
        return defs

    # ── 加载内置 Skills ─────────────────────────────────────────────

    def load_builtins(self) -> None:
        """加载内置 Python Skills。

        每个内置 Skill 放在 renayo/skills/ 下，继承 BaseSkill，
        在 __init__.py 的 BUILTIN_SKILLS 中注册。
        """
        # 延迟导入避免循环
        from . import paper_radar, trend_track, code_assist
        from . import web_search, translate, schedule, shell, image_gen
        from . import file_ops, browser

        builtins: list[BaseSkill] = [
            paper_radar.PaperRadarSkill(),
            trend_track.TrendTrackSkill(),
            code_assist.CodeAssistSkill(),
            web_search.WebSearchSkill(),
            translate.TranslateSkill(),
            schedule.ScheduleSkill(),
            shell.ShellSkill(),
            image_gen.ImageGenSkill(),
            file_ops.FileOpsSkill(),
            browser.BrowserSkill(),
        ]

        for skill in builtins:
            if skill.name:
                self._skills[skill.name] = skill
                sd = skill.to_def()
                self._defs[skill.name] = sd

    # ── 加载用户 Skills ─────────────────────────────────────────────

    def load_user_skills(self) -> None:
        """加载用户/自进化 Skills（从 SKILL.md）。"""
        for sd in self._defs.values():
            if sd.name in self._skills:
                continue  # 已加载
            body = self._user_docs.get(sd.name, "")
            self._skills[sd.name] = UserSkill(sd, body)

    # ── 匹配 ─────────────────────────────────────────────────────────

    def match(self, user_input: str) -> list[BaseSkill]:
        """根据用户输入匹配最相关的 Skills。

        Returns:
            按匹配度降序排列的 Skill 列表。
        """
        scored: list[tuple[float, BaseSkill]] = []
        for skill in self._skills.values():
            score = skill.match(user_input)
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored]

    # ── 工具汇总 ────────────────────────────────────────────────────

    def get_all_tools(self) -> list[dict[str, Any]]:
        """汇总所有已加载 Skills 的工具 → OpenAI function calling schema。

        Returns:
            OpenAI 格式的工具定义列表。
        """
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        for skill in self._skills.values():
            for st in skill.get_tools():
                if st.name not in seen:
                    tools.append(st.to_openai_schema())
                    seen.add(st.name)
        return tools

    def get_tool_handler(self, tool_name: str) -> Any | None:
        """根据工具名查找执行函数。"""
        for skill in self._skills.values():
            for st in skill.get_tools():
                if st.name == tool_name:
                    return st.handler
        return None

    # ── 状态 ─────────────────────────────────────────────────────────

    @property
    def skill_count(self) -> int:
        return len(self._skills)

    @property
    def builtin_count(self) -> int:
        return sum(1 for s in self._skills.values() if not isinstance(s, UserSkill))

    @property
    def user_count(self) -> int:
        return sum(1 for s in self._skills.values() if isinstance(s, UserSkill))

    def list_skills(self) -> list[dict[str, Any]]:
        """列出所有 Skills 摘要。"""
        result = []
        for name, skill in self._skills.items():
            sd = self._defs.get(name)
            result.append({
                "name": name,
                "description": skill.description,
                "trigram": skill.trigram,
                "tools": sd.tool_names if sd else [],
                "usage": sd.usage_count if sd else 0,
                "created": sd.created if sd else "manual",
                "is_user": isinstance(skill, UserSkill),
            })
        return sorted(result, key=lambda x: x["usage"], reverse=True)
