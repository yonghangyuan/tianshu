"""天枢 Rich CLI — 对标 Claude Code 的终端体验。

特性：
  - 流式输出（ContentDelta 逐字渲染）
  - Rich Markdown（代码高亮、表格、列表）
  - 工具调用卡片（● name / ⎿ result）
  - 推理链折叠显示
  - 统计栏

用法：
  tianshu-cli           # 交互模式
  tianshu-cli "你好"    # 单次执行
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from tianshu import __version__

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.table import Table
from rich import box

from tianshu.sdk.models import (
    AgentRequest, AgentContext,
    ContentDelta, ReasoningDelta,
    ToolCallStart, ToolCallResult,
    ToolCallConfirm, StreamDone, StreamError,
)


# ═══════════════════════════════════════════════════════════════════════════
# Console
# ═══════════════════════════════════════════════════════════════════════════

console = Console(highlight=False)


# ═══════════════════════════════════════════════════════════════════════════
# 渲染器
# ═══════════════════════════════════════════════════════════════════════════

class StreamRenderer:
    """流式事件 → Rich 终端渲染。

    策略：
      - ContentDelta: 缓冲不打印，避免与 Markdown 冲突
      - ToolCallStart/Result: 实时打印（工具调用是关键状态变化）
      - StreamDone: 用 Rich Markdown 渲染完整回复
    """

    def __init__(self) -> None:
        self._content_buf: list[str] = []
        self._reasoning_buf: list[str] = []
        self._tool_count = 0
        self._show_reasoning = False  # 默认折叠，/think 切换
        self._t0 = time.time()
        self._flushed_len = 0  # 已 flush 的字符数
        self._last_cost: dict = {}  # /cost 查询用

    def reset(self) -> None:
        self._content_buf.clear()
        self._reasoning_buf.clear()
        self._tool_count = 0
        self._t0 = time.time()
        self._flushed_len = 0

    def handle(self, event) -> bool:
        """处理一个 streaming 事件，渲染到终端。"""
        if isinstance(event, ContentDelta):
            self._content_buf.append(event.text)
            # 段落级流式：检测到空行（段落边界）时 flush 已完成的段落
            full = "".join(self._content_buf)
            # 找最后一个 \n\n（段落边界）
            last_para_end = full.rfind("\n\n")
            if last_para_end > self._flushed_len:
                # flush 到段落边界之前的完整段落
                ready = full[self._flushed_len:last_para_end + 2]
                self._flushed_len = last_para_end + 2
                try:
                    console.print(Markdown(ready.strip(), code_theme="one-dark"))
                except Exception:
                    console.print(ready.strip())
            return True

        elif isinstance(event, ReasoningDelta):
            self._reasoning_buf.append(event.text)
            if self._show_reasoning:
                console.print(Text(event.text, style="dim italic"), end="")
            return True

        elif isinstance(event, ToolCallStart):
            # 工具调用前，flush 所有未渲染的内容（防止被截断）
            full = "".join(self._content_buf)
            if self._flushed_len < len(full):
                remaining = full[self._flushed_len:].strip()
                if remaining:
                    try:
                        console.print(Markdown(remaining, code_theme="one-dark"))
                    except Exception:
                        console.print(remaining)
                self._flushed_len = len(full)

            self._tool_count += 1
            self._tool_phase = True
            args_summary = _format_tool_args(event.tool_name, event.tool_args)
            console.print(Text(
                f"  ● {event.tool_name}({args_summary})",
                style="bold cyan",
            ))
            return True

        elif isinstance(event, ToolCallResult):
            icon = "✓" if event.success else "✗"
            style = "green" if event.success else "red"
            output_preview = event.output[:120].replace("\n", " ")
            console.print(Text(
                f"  ⎿ {icon} {output_preview}  {event.elapsed_ms}ms",
                style="dim",
            ))
            return True

        elif isinstance(event, ToolCallConfirm):
            console.print()
            console.print(Panel(
                f"[bold yellow]⚠ {event.tool_name}[/bold yellow] 需要确认\n"
                f"[dim]{_format_tool_args(event.tool_name, event.tool_args)}[/dim]",
                border_style="yellow",
                padding=(1, 2),
            ))
            console.print("[bold]y[/bold] 允许  [bold]n[/bold] 拒绝  [bold]a[/bold] 始终允许 › ", end="")
            return True

        elif isinstance(event, StreamDone):
            self._last_cost = {
                "prompt": event.prompt_tokens,
                "completion": event.completion_tokens,
                "elapsed": event.elapsed_ms / 1000.0,
                "model": event.model_used,
            }
            # 渲染剩余内容（段落边界之后的部分）
            full = "".join(self._content_buf)
            remaining = full[self._flushed_len:].strip()
            if remaining:
                try:
                    console.print(Markdown(remaining, code_theme="one-dark"))
                except Exception:
                    console.print(remaining)
            elif self._flushed_len == 0 and full.strip():
                # 短回复（无段落边界）：直接渲染
                console.print()
                try:
                    console.print(Markdown(full.strip(), code_theme="one-dark"))
                except Exception:
                    console.print(full.strip())
            return True

        elif isinstance(event, StreamError):
            console.print()
            console.print(Panel(
                Text(event.message, style="bold"),
                title="[bold red]✗ Error[/bold red]",
                border_style="red",
                padding=(1, 2),
            ))
            return False

        return True

    def toggle_reasoning(self) -> bool:
        self._show_reasoning = not self._show_reasoning
        return self._show_reasoning


def _print_content_markdown(text: str) -> None:
    """用 Rich Markdown 渲染回复内容。"""
    console.print()
    try:
        md = Markdown(text, code_theme="one-dark")
        console.print(md)
    except Exception:
        console.print(text)


# ═══════════════════════════════════════════════════════════════════════════
# 渲染辅助
# ═══════════════════════════════════════════════════════════════════════════

def _format_tool_args(name: str, args: dict) -> str:
    """格式化工具参数为紧凑的一行。"""
    if not args:
        return ""
    # shell_exec: 显示命令
    if name == "shell_exec" and "command" in args:
        return args["command"][:80]
    # web_search: 显示 query
    if name == "web_search" and "query" in args:
        return args["query"][:60]
    # remember_fact: 显示 key
    if name == "remember_fact" and "key" in args:
        return f"{args['key']}={args.get('value', '')[:40]}"
    # 通用：显示 JSON
    import json as _json
    try:
        s = _json.dumps(args, ensure_ascii=False)
        return s[:80]
    except Exception:
        return str(args)[:80]


def _print_done(event: StreamDone, tool_count: int, t0: float,
                reasoning_buf: list[str] | None = None,
                show_reasoning: bool = False) -> None:
    """打印完成统计行 + 折叠的思考过程。"""
    # 思考折叠提示
    if reasoning_buf and not show_reasoning:
        full = "".join(reasoning_buf)
        first_line = full.split("\n")[0][:120] if full else ""
        remaining = len(full) - len(first_line)
        if remaining > 10:
            console.print(Panel(
                Text(f"{first_line}...", style="dim italic"),
                title=f"[dim]Thinking ({len(full)} chars)[/dim]",
                subtitle="[dim]/think 展开[/dim]",
                border_style="dim",
                padding=(0, 1),
            ))

    elapsed = time.time() - t0
    parts = [
        f"⎿  {event.decision_id[-8:]}",
        event.model_used,
    ]
    if tool_count:
        parts.append(f"{tool_count} tools")
    parts.append(f"{elapsed:.1f}s")
    if event.prompt_tokens:
        parts.append(
            f"{event.prompt_tokens // 1000}K↑"
            f"{event.completion_tokens // 1000}K↓"
        )
    if event.error:
        parts.append(event.error)

    console.print(Text(f"  {'  '.join(parts)}", style="dim"))


# ═══════════════════════════════════════════════════════════════════════════
# Splash — 从 splash.py 移植（Rich 版本）
# ═══════════════════════════════════════════════════════════════════════════

def render_splash_rich(
    models: list[dict[str, Any]] | None = None,
    tool_count: int = 0,
    skill_count: int = 0,
    model_count: int = 0,
    plugin_count: int = 0,
) -> None:
    """Rich 版本启动页。"""
    width = min(shutil.get_terminal_size().columns, 90)

    # Logo
    logo = """  ████████╗ ██╗ █████╗ ███╗  ██╗  ███████╗██╗  ██╗██╗  ██╗
  ╚══██╔══╝██╔╝██╔══██╗████╗ ██║  ██╔════╝██║  ██║██║  ██║
     ██║  ██║ ███████║██╔██╗██║  ███████╗███████║██║  ██║
     ██║  ██║ ██╔══██║██║╚████║  ╚════██║██╔══██║██║  ██║
     ██║  ██║ ██║  ██║██║ ╚███║  ███████║██║  ██║╚██████╔╝
     ╚═╝  ╚═╝ ╚═╝  ╚═╝╚═╝  ╚══╝  ╚══════╝╚═╝  ╚═╝ ╚═════╝"""

    cloud_top = "~*~  ☁  祥 云  ·  瑞 霭  ·  天 枢  ☁  ~*~"
    cloud_bot = "~*~  ☁  北 斗 七 星  ·  枢 纽 定 乾 坤  ☁  ~*~"
    subtitle = "北斗七星第一星  ·  主司枢纽与导向  ·  中国本土自主 AI Agent 框架"

    console.print()
    console.print(Text(cloud_top, style="dim cyan", justify="center"))
    console.print(Text(logo, style="bold yellow"))
    console.print(Text(subtitle, style="dim cyan", justify="center"))
    console.print()

    # Info table
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 3))
    table.add_column(style="bold cyan", width=10)
    table.add_column(style="dim")

    now = time.strftime("%Y-%m-%d %H:%M")
    table.add_row("天枢", f"v{__version__}  ·  {now}")

    if models:
        model_strs = []
        for m in models[:6]:
            name = m.get("name", "?")
            tags = m.get("tags", set())
            icon = "[R]" if "reasoning" in tags else "[C]" if "fast" in tags else "   "
            model_strs.append(f"{icon} {name}")
        table.add_row("模型", "  ".join(model_strs))
    else:
        table.add_row("模型", "未配置 — 输入 --setup 开始")

    table.add_row("能力", f"{tool_count} 工具 · {skill_count} Skills · {model_count} 模型 · {plugin_count} 插件")
    table.add_row("架构", "[red]☰ 天爻[/red]·规律  [yellow]☷ 人爻[/yellow]·目的  [cyan]☷ 地爻[/cyan]·物质")

    console.print(table)
    console.print("─" * min(width, 78), style="dim")
    console.print(Text(cloud_bot, style="dim cyan", justify="center"))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
# 单次聊天
# ═══════════════════════════════════════════════════════════════════════════

async def chat_once(
    core,  # AgentCore
    text: str,
    ctx: AgentContext | None = None,
    show_reasoning: bool = False,
) -> AgentContext:
    """单次对话——流式渲染。"""
    renderer = StreamRenderer()
    renderer._show_reasoning = show_reasoning

    req = AgentRequest(input=text, task_type="conversation")

    if ctx is None:
        ctx = AgentContext()

    # 用户输入提示
    console.print(Text(f"▸ {text}", style="bold #60a5fa"))
    console.print()

    async for event in core.run_stream(req, ctx=ctx):
        if isinstance(event, ToolCallConfirm):
            _handle_confirm(event, core)
        else:
            renderer.handle(event)

    console.print()
    return ctx


def _handle_confirm(event: ToolCallConfirm, core) -> None:
    """处理工具确认事件——← → 选择，Enter 确认。"""
    import sys as _sys

    perm_label = {0: "SAFE", 1: "READ", 2: "WRITE", 3: "DANGER"}
    level = event.permission_level
    label = perm_label.get(level, str(level))
    color = "yellow" if level == 2 else "red"

    options = [
        ("y", "允许", "green"),
        ("n", "拒绝", "red"),
        ("a", f"始终允许 {event.tool_name}", "yellow"),
    ]
    idx = 0  # 默认选中 "允许"

    def _draw():
        """重绘选择行。"""
        parts = []
        for i, (key, label_str, c) in enumerate(options):
            if i == idx:
                parts.append(f"[bold {c}]▶ {label_str}[/bold {c}]")
            else:
                parts.append(f"[dim {c}]  {label_str}[/dim {c}]")
        # 清除当前行并重绘
        _sys.stdout.write("\r\033[K")
        console.print("  " + "    ".join(parts), end="")
        _sys.stdout.flush()

    console.print()
    console.print(Panel(
        f"[bold {color}]⚠ {event.tool_name}[/bold {color}] 需要确认 "
        f"[[dim]{label}[/dim]]\n"
        f"[dim]{_format_tool_args(event.tool_name, event.tool_args)}[/dim]",
        border_style=color,
        padding=(1, 2),
    ))
    console.print("[dim]← → 选择  Enter 确认  Esc 取消[/dim]")
    _draw()

    # ← → Enter 导航
    while True:
        key = _get_keypress()
        if key == "left":
            idx = (idx - 1) % len(options)
            _draw()
        elif key == "right":
            idx = (idx + 1) % len(options)
            _draw()
        elif key == "enter":
            _sys.stdout.write("\n")
            break
        elif key == "esc":
            idx = 1  # 默认拒绝
            _sys.stdout.write("\n")
            break
        elif key in ("y", "n", "a"):
            # 仍然支持键盘快捷键
            idx = {"y": 0, "n": 1, "a": 2}[key]
            _sys.stdout.write(f"{key}\n")
            break

    choice = options[idx][0]
    if choice == "a":
        core._confirm_allowed = True
        if not hasattr(core, '_permission_whitelist'):
            core._permission_whitelist = set()
        core._permission_whitelist.add(event.tool_name)
        core.confirm_tool(True)
    elif choice == "y":
        core.confirm_tool(True)
    else:
        core.confirm_tool(False)


def _get_keypress() -> str:
    """读取单个按键——跨平台（Windows msvcrt / Unix termios）。"""
    import sys as _sys
    if _sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()
        if ch == "\x00" or ch == "\xe0":
            ch2 = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(ch2, ch + ch2)
        if ch == "\r":
            return "enter"
        if ch == "\x1b":
            return "esc"
        return ch
    else:
        import termios, tty
        fd = _sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = _sys.stdin.read(1)
            if ch == "\x1b":
                seq = _sys.stdin.read(2)
                return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(seq, "esc")
            if ch == "\r":
                return "enter"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
