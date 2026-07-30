"""ToolRegistry —— 集中管理所有工具。

扫描所有 Skill，统一注册、查询、按模式过滤。
替换散落在 Skill.get_tools() 中的碎片化注册。
"""

from __future__ import annotations

from typing import Any


class ToolInfo:
    """单个工具的完整信息。"""
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Any,
        permission: int = 0,
        skill_name: str = "",
        category: str = "general",
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.permission = permission  # 0=SAFE 1=READ 2=WRITE 3=DANGER
        self.skill_name = skill_name
        self.category = category
        self.call_count: int = 0
        self.error_count: int = 0

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        perm_labels = {0: "SAFE", 1: "READ", 2: "WRITE", 3: "DANG"}
        return (
            f"Tool({self.name} [{perm_labels.get(self.permission, '?')}] "
            f"← {self.skill_name})"
        )


class ToolRegistry:
    """工具注册中心——唯一的工具入口。

    用法:
        reg = ToolRegistry()
        reg.scan_skills(core.skills.loader)
        tools = reg.get_tools(mode="normal")  # 按模式过滤
        reg.execute("browse", {"url": "..."})
    """

    def __init__(self):
        self._tools: dict[str, ToolInfo] = {}
        self._by_skill: dict[str, list[str]] = {}  # skill_name → [tool_names]

    # ── 注册 ─────────────────────────────────────────────────

    def register(self, tool: ToolInfo) -> None:
        self._tools[tool.name] = tool
        if tool.skill_name not in self._by_skill:
            self._by_skill[tool.skill_name] = []
        self._by_skill[tool.skill_name].append(tool.name)

    def scan_skills(self, loader) -> int:
        """从 SkillLoader 扫描所有 Skill，自动注册工具。"""
        count = 0
        for skill in loader._skills.values():
            for tool in skill.get_tools():
                info = ToolInfo(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    handler=tool.handler,
                    permission=getattr(tool, 'permission_level', 0),
                    skill_name=skill.name,
                )
                self.register(info)
                count += 1
        return count

    # ── 查询 ─────────────────────────────────────────────────

    def get(self, name: str) -> ToolInfo | None:
        return self._tools.get(name)

    def get_tools(self, mode: str = "normal") -> list[dict]:
        """获取工具列表（OpenAI schema 格式），按模式过滤。

        normal: 全工具
        plan:   只读（SAFE + READ 级别）
        auto:   全工具
        """
        schemas = []
        for tool in self._tools.values():
            if mode == "plan" and tool.permission >= 2:
                continue  # plan 模式跳过高风险工具
            schemas.append(tool.to_openai_schema())
        return schemas

    def list_all(self) -> list[ToolInfo]:
        return list(self._tools.values())

    def list_by_skill(self, skill_name: str) -> list[ToolInfo]:
        names = self._by_skill.get(skill_name, [])
        return [self._tools[n] for n in names if n in self._tools]

    # ── 统计 ─────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self._tools)

    @property
    def skill_count(self) -> int:
        return len(self._by_skill)

    def stats(self) -> str:
        """人类可读的工具统计。"""
        lines = [f"工具: {self.count} 个 · 技能: {self.skill_count} 个\n"]
        for skill_name in sorted(self._by_skill.keys()):
            tools = self.list_by_skill(skill_name)
            perm_icons = {0: "🟢", 1: "🔵", 2: "🟡", 3: "🔴"}
            tool_strs = [
                f"{perm_icons.get(t.permission, '⚪')}{t.name}"
                for t in tools
            ]
            lines.append(f"  {skill_name}: {', '.join(tool_strs)}")
        return "\n".join(lines)
