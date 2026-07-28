"""Skill 基类 + 数据定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SkillTool:
    """Skill 提供的工具——OpenAI function calling 兼容。"""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[..., Any]  # 同步或异步执行函数
    permission_level: int = 0  # PermissionLevel: 0=SAFE, 1=READ, 2=WRITE, 3=DANGER

    def to_openai_schema(self) -> dict[str, Any]:
        """转为 OpenAI function calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class SkillDef:
    """Skill 元数据（从 SKILL.md 或 Python 类解析）。"""
    name: str
    description: str
    trigram: str = "人"         # "地" | "人" | "天" | "地+人" 等
    tool_names: list[str] = field(default_factory=list)
    trigger_keywords: list[str] = field(default_factory=list)
    version: int = 1
    usage_count: int = 0
    created: str = "manual"    # "auto" | "manual"
    source_path: str = ""


class BaseSkill(ABC):
    """Skill 基类——所有内置 Skill 继承此类。

    子类只需覆写：
      - name / description / trigram / trigger_keywords
      - get_tools() → list[SkillTool]

    用户自进化的 Skill（来自 SKILL.md）用 UserSkill 包装。
    """

    name: str = ""
    description: str = ""
    trigram: str = "人"
    trigger_keywords: list[str] = []

    @abstractmethod
    def get_tools(self) -> list[SkillTool]:
        """返回此 Skill 提供的工具列表。"""
        ...

    def match(self, user_input: str) -> float:
        """检查用户输入是否触发此 Skill。返回 0~1 匹配度。"""
        if not self.trigger_keywords:
            return 0.0
        inp = user_input.lower()
        hits = sum(1 for kw in self.trigger_keywords if kw.lower() in inp)
        return hits / len(self.trigger_keywords) if self.trigger_keywords else 0.0

    def to_def(self) -> SkillDef:
        """导出为 SkillDef 元数据。"""
        return SkillDef(
            name=self.name,
            description=self.description,
            trigram=self.trigram,
            tool_names=[t.name for t in self.get_tools()],
            trigger_keywords=self.trigger_keywords,
            created="manual",
        )


class UserSkill(BaseSkill):
    """用户自进化 Skill——从 SKILL.md 加载，工具由 LLM 描述但未实现。

    与内置 Skill 的差异：工具不是 Python 函数，而是自然语言描述。
    Agent 看到这些工具描述后，自己组合现有工具来实现。
    """

    def __init__(self, skill_def: SkillDef, body: str = "") -> None:
        self.name = skill_def.name
        self.description = skill_def.description
        self.trigram = skill_def.trigram
        self.trigger_keywords = skill_def.trigger_keywords
        self._def = skill_def
        self._body = body

    def get_tools(self) -> list[SkillTool]:
        """用户 Skill 的工具是自然语言描述——让 Agent 自行组合。"""
        return []

    def to_def(self) -> SkillDef:
        return self._def

    @property
    def body(self) -> str:
        """Skill 正文（Markdown 描述）。注入到系统提示词供 Agent 参考。"""
        return self._body
