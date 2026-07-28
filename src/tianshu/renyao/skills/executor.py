"""Skill 执行器 — 注册 + 执行 Skill 工具，同时记录到观测器。"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from .base import SkillTool
from .loader import SkillLoader
from .observer import SkillObserver


class SkillExecutor:
    """执行 Skill 工具。

    用法:
        executor = SkillExecutor(loader, observer)
        executor.register_all()
        result = await executor.execute("search_papers", {"query": "RL"})
    """

    def __init__(self, loader: SkillLoader, observer: SkillObserver | None = None) -> None:
        self._loader = loader
        self._observer = observer
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register_all(self) -> None:
        """注册所有已加载 Skills 的工具到执行表。"""
        for skill in self._loader._skills.values():
            for tool in skill.get_tools():
                self._handlers[tool.name] = tool.handler

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """执行指定工具。

        Args:
            tool_name: 工具名
            arguments: 工具参数

        Returns:
            工具执行结果的字符串表示。
        """
        handler = self._handlers.get(tool_name)
        if handler is None:
            # 尝试从 loader 查找
            handler = self._loader.get_tool_handler(tool_name)
        if handler is None:
            return f"未知工具: {tool_name}"

        try:
            # 支持同步和异步 handler
            result = handler(**arguments)
            if asyncio.iscoroutine(result):
                result = await result
            output = str(result)

            # 记录到观测器
            if self._observer:
                self._observer.add_call(tool_name, True)

            return output
        except Exception as e:
            if self._observer:
                self._observer.add_call(tool_name, False)
            return f"工具执行失败: {e}"
