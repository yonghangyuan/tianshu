"""天枢命令系统 — 对标 Claude Code 的 / 命令。

CommandRegistry 提供统一的命令注册/查找/补全，CLI 和 TUI 共享。

用法:
    registry = CommandRegistry()

    @registry.register("help", aliases=["/help", "--help"], category="info")
    async def cmd_help(ctx: CommandContext) -> None:
        ctx.print_help()

    result = registry.handle("/help", context)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class Command:
    """一条命令定义。"""
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    category: str = "general"  # info, config, session, debug, conversation
    handler: Callable[..., Any] | None = None
    # 是否需要 AgentCore 注入
    needs_core: bool = False
    # 参数帮助
    usage: str = ""


@dataclass
class CommandContext:
    """命令执行上下文——传递给每个 handler。"""
    core: Any = None  # AgentCore 实例
    ctx: Any = None   # AgentContext 实例
    args: str = ""    # 命令参数（去掉命令名后的部分）

    def print(self, *args, **kwargs) -> None:
        """Rich 输出辅助。"""
        from rich.console import Console as _RC
        c = _RC(highlight=False)
        c.print(*args, **kwargs)


class CommandRegistry:
    """命令注册中心。

    - 注册/查找命令
    - 模糊匹配（/mod → /models）
    - 生成补全列表
    """

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(
        self,
        name: str,
        *,
        aliases: list[str] | None = None,
        description: str = "",
        category: str = "general",
        needs_core: bool = False,
        usage: str = "",
    ):
        """装饰器工厂：注册命令。

        @registry.register("models", aliases=["/models"], category="info")
        async def cmd_models(ctx: CommandContext) -> None: ...
        """
        def decorator(fn):
            cmd = Command(
                name=name,
                aliases=aliases or [],
                description=description,
                category=category,
                handler=fn,
                needs_core=needs_core,
                usage=usage,
            )
            self._commands[name] = cmd
            return fn
        return decorator

    def find(self, user_input: str) -> Command | None:
        """查找命令——支持模糊匹配。

        匹配逻辑：
        1. 精确匹配 name（如 "help"）
        2. 精确匹配 alias（如 "/help", "--help"）
        3. 前缀模糊匹配（如 "mod" → "models"）
        """
        stripped = user_input.strip().lstrip("/").lstrip("-")

        # 精确匹配
        for cmd in self._commands.values():
            if stripped == cmd.name:
                return cmd
            for alias in cmd.aliases:
                if stripped == alias.lstrip("/").lstrip("-"):
                    return cmd

        # 模糊匹配（取第一个名字前缀匹配的）
        matches = [
            cmd for cmd in self._commands.values()
            if cmd.name.startswith(stripped)
        ]
        if len(matches) == 1:
            return matches[0]

        return None

    def list_all(self) -> list[Command]:
        """列出所有命令（按分类排序）。"""
        order = {"info": 0, "config": 1, "session": 2, "conversation": 3, "debug": 4, "general": 5}
        return sorted(
            self._commands.values(),
            key=lambda c: (order.get(c.category, 5), c.name),
        )

    def list_by_category(self, category: str) -> list[Command]:
        return [c for c in self._commands.values() if c.category == category]

    @property
    def command_names(self) -> list[str]:
        """所有命令名——供 prompt_toolkit 补全。"""
        names = []
        for cmd in self._commands.values():
            names.append(f"/{cmd.name}")
            names.extend(cmd.aliases)
        return sorted(set(names))

    async def handle(
        self,
        user_input: str,
        context: CommandContext | None = None,
    ) -> bool:
        """执行命令。

        Returns:
            True 如果命令被处理，False 如果不是命令。
        """
        if context is None:
            context = CommandContext()

        # 解析命令名和参数
        parts = user_input.strip().split(maxsplit=1)
        cmd_name = parts[0]
        context.args = parts[1] if len(parts) > 1 else ""

        cmd = self.find(cmd_name)
        if cmd is None:
            return False

        if cmd.handler is None:
            return False

        # 执行 handler（支持 sync 和 async）
        import asyncio
        result = cmd.handler(context)
        if asyncio.iscoroutine(result):
            await result

        return True


# ═══════════════════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════════════════

_registry: CommandRegistry | None = None


def get_registry() -> CommandRegistry:
    global _registry
    if _registry is None:
        _registry = CommandRegistry()
        _register_builtins(_registry)
    return _registry


def _register_builtins(reg: CommandRegistry) -> None:
    """注册内置命令（占位 handler，具体逻辑在 main.py / tui.py 中实现）。"""

    @reg.register("help", aliases=["/help", "--help"], category="info",
                  description="显示帮助信息")
    async def _help(ctx: CommandContext) -> None:
        ctx.print("[bold]天枢 Agent 命令[/bold]")
        ctx.print()
        from rich.table import Table
        from rich import box
        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        t.add_column(style="bold cyan", width=16)
        t.add_column(style="dim")
        for cmd in reg.list_all():
            aliases = ", ".join(c for c in cmd.aliases if c.startswith("/"))
            name_col = f"/{cmd.name}  {aliases}"
            t.add_row(name_col, cmd.description)
        ctx.print(t)

    @reg.register("models", aliases=["/models", "--models"], category="info",
                  description="列出已注册的 AI 模型")
    async def _models(ctx: CommandContext) -> None:
        ctx.print("[dim]模型列表（由主循环处理）[/dim]")

    @reg.register("model", aliases=["/model"], category="config",
                  description="切换默认模型 (e.g. /model v4-pro)")
    async def _model(ctx: CommandContext) -> None:
        ctx.print("[dim]模型切换（由主循环处理）[/dim]")

    @reg.register("skills", aliases=["/skills", "--skills"], category="info",
                  description="列出可用 Skills")
    async def _skills(ctx: CommandContext) -> None:
        ctx.print("[dim]Skills 列表（由主循环处理）[/dim]")

    @reg.register("audit", aliases=["/audit", "--audit"], category="debug",
                  description="查看审计记录")
    async def _audit(ctx: CommandContext) -> None:
        ctx.print("[dim]审计记录（由主循环处理）[/dim]")

    @reg.register("memory", aliases=["/memory", "--memory"], category="debug",
                  description="查看长期记忆")
    async def _memory(ctx: CommandContext) -> None:
        ctx.print("[dim]记忆状态（由主循环处理）[/dim]")

    @reg.register("think", aliases=["/think", "--think"], category="debug",
                  description="查看最近推理链")
    async def _think(ctx: CommandContext) -> None:
        ctx.print("[dim]推理链（由主循环处理）[/dim]")

    @reg.register("plugin", aliases=["/plugin", "--plugin"], category="debug",
                  description="插件管理")
    async def _plugin(ctx: CommandContext) -> None:
        ctx.print("[dim]插件管理（由主循环处理）[/dim]")

    @reg.register("cron", aliases=["/cron"], category="config",
                  description="定时任务管理 (/cron list, /cron add)")
    async def _cron(ctx: CommandContext) -> None:
        ctx.print("[dim]定时任务（由主循环处理）[/dim]")

    @reg.register("setup", aliases=["/setup", "--setup"], category="config",
                  description="配置 API Key")
    async def _setup(ctx: CommandContext) -> None:
        ctx.print("[dim]API Key 配置（由主循环处理）[/dim]")

    @reg.register("clear", aliases=["/clear"], category="general",
                  description="清屏")
    async def _clear(ctx: CommandContext) -> None:
        from rich.console import Console as _RC
        _RC(highlight=False).clear()

    @reg.register("exit", aliases=["/exit", "quit", "q"], category="general",
                  description="退出")
    async def _exit(ctx: CommandContext) -> None:
        ctx.print("[dim]退出（由主循环处理）[/dim]")

    @reg.register("session", aliases=["/session"], category="session",
                  description="会话管理 (/session list, /session resume)")
    async def _session(ctx: CommandContext) -> None:
        ctx.print("[dim]会话管理（Phase 4 实现）[/dim]")

    @reg.register("plan", aliases=["/plan"], category="conversation",
                  description="强制使用 Planner 规划任务 (/plan <任务描述>)")
    async def _plan_cmd(ctx: CommandContext) -> None:
        ctx.print("[dim]Planner 规划（由主循环处理）[/dim]")

    @reg.register("mode", aliases=["/mode"], category="config",
                  description="循环切换模式: normal → auto → plan (Shift+Tab)")
    async def _mode_cmd(ctx: CommandContext) -> None:
        ctx.print("[dim]模式切换（由主循环处理）[/dim]")

    @reg.register("preset", aliases=["/preset"], category="config",
                  description="循环切换预设: standard → minimal → code(PTC) (F2)")
    async def _preset_cmd(ctx: CommandContext) -> None:
        ctx.print("[dim]预设切换（由主循环处理）[/dim]")

    @reg.register("config", aliases=["/config"], category="config",
                  description="配置管理 (/config show)")
    async def _config(ctx: CommandContext) -> None:
        ctx.print("[dim]配置管理（待实现）[/dim]")

    @reg.register("mcp", aliases=["/mcp"], category="config",
                  description="MCP 服务器管理 (/mcp servers|tools|reload|connect|disconnect)")
    async def _mcp_cmd(ctx: CommandContext) -> None:
        ctx.print("[dim]MCP 管理（由主循环处理）[/dim]")
