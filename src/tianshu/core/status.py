"""CLI structured activity blocks — Claude Code style.

Simple, stateless, race-free: each call writes a complete line to stderr.
No global state, no async tasks, no timers (timer lives in main.py separately).

Format:
  ● Action(target)        — operation start
  ⎿  result               — operation result
  ⚠  error                — failure

Rich 模式：当 Rich Console 可用时，输出 Rich renderable 而非 ANSI。
"""

import sys
import time
from typing import Any

_ENABLED = sys.stderr.isatty()
_USE_RICH = False
_rich_console = None


def set_rich_mode(console=None) -> None:
    """切换到 Rich 渲染模式。传入 Rich Console 实例。"""
    global _USE_RICH, _rich_console
    _USE_RICH = True
    _rich_console = console

# ── ANSI ────────────────────────────────────────────────────────────────

def _c(code: str, s: str) -> str:
    return f"{code}{s}\033[0m" if _ENABLED else s

GRAY   = "\033[90m"
GREEN  = "\033[32m"
CYAN   = "\033[36m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def _fmt_dur(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    elif ms < 60000:
        return f"{ms/1000:.1f}s"
    else:
        m = int(ms / 60000)
        return f"{m}m{(ms%60000)/1000:.0f}s"

# ── Public API ──────────────────────────────────────────────────────────

def route(model: str, elapsed_ms: int) -> None:
    """Route completed."""
    if _USE_RICH and _rich_console:
        from rich.text import Text
        _rich_console.print(Text(
            f"  ● Route({model})  {_fmt_dur(elapsed_ms)}",
            style="dim cyan",
        ))
    else:
        _line(CYAN, "Route", model, elapsed_ms)


def tool(name: str, detail: str, elapsed_ms: int) -> None:
    """Tool executed."""
    if _USE_RICH and _rich_console:
        from rich.text import Text
        _rich_console.print(Text(
            f"  ● {name}({detail[:80]})  {_fmt_dur(elapsed_ms)}",
            style="green",
        ))
    else:
        _line(GREEN, name, detail[:80], elapsed_ms)


def done(decision_id: str, model: str, tools: int, elapsed_ms: int) -> None:
    """Final decision record."""
    if _USE_RICH and _rich_console:
        from rich.text import Text
        short_id = decision_id[-8:]
        _rich_console.print(Text(
            f"  ⎿  {short_id}  {model}  {tools} tools  {_fmt_dur(elapsed_ms)}",
            style="dim",
        ))
    else:
        short_id = decision_id[-8:]
        _write(f"  {GRAY}⎿  {short_id}{RESET}  {model}  {GRAY}{tools} tools  {_fmt_dur(elapsed_ms)}{RESET}")

def warn(msg: str) -> None:
    _write(f"  {YELLOW}⚠{RESET}  {msg}")

def error(msg: str) -> None:
    _write(f"  {RED}⚠{RESET}  {msg}")

def info(msg: str) -> None:
    _write(f"  {GRAY}{msg}{RESET}")

# ── Internal ─────────────────────────────────────────────────────────────

def _line(color: str, action: str, detail: str, elapsed_ms: int) -> None:
    _write(f"  {BOLD}{color}●{RESET} {action}({BOLD}{detail}{RESET})  {GRAY}{_fmt_dur(elapsed_ms)}{RESET}")

def _write(text: str) -> None:
    if not _ENABLED:
        return
    sys.stderr.write(text + "\n")
    sys.stderr.flush()
