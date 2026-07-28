"""终端交互菜单 — 键盘导航（↑↓选择 / Enter确认 / q退出）。

零外部依赖，Windows 用 msvcrt，Unix 用 termios。
"""

from __future__ import annotations

import sys
from typing import Any


def _get_key() -> str:
    """读取单个按键。处理方向键转义序列。"""
    if sys.platform == "win32":
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
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(seq, "esc")
            if ch == "\r":
                return "enter"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select_menu(title: str, options: list[dict[str, Any]], default_index: int = 0) -> int | None:
    """显示键盘导航选择菜单。

    Args:
        title: 菜单标题
        options: [{"label": "显示文本", "desc": "描述", "value": any}, ...]
        default_index: 默认高亮项索引

    Returns:
        选中项的索引，用户按 q 退出则返回 None。

    按键:
        ↑ ↓  → 导航
        Enter / →  → 确认选择
        q / Esc    → 退出
    """
    index = default_index
    n = len(options)

    def _render() -> None:
        """绘制菜单。"""
        _clear_lines(n + 3)
        print(f"\n  {title}")
        print(f"  {'─' * 50}")
        for i, opt in enumerate(options):
            cursor = "❯" if i == index else " "
            desc = opt.get("desc", "")
            label = opt["label"]
            if i == index:
                print(f"  {cursor} \033[1;36m{label}\033[0m  \033[2m{desc}\033[0m")
            else:
                print(f"  {cursor} {label}  \033[2m{desc}\033[0m")
        print(f"\n  ↑↓ 选择  Enter / → 确认  q 退出")

    _render()
    while True:
        key = _get_key()
        if key == "up":
            index = (index - 1) % n
            _render()
        elif key == "down":
            index = (index + 1) % n
            _render()
        elif key in ("enter", "right"):
            _clear_lines(n + 4)
            return index
        elif key in ("q", "esc"):
            _clear_lines(n + 4)
            return None


def _clear_lines(count: int) -> None:
    """清除屏幕上 N 行。"""
    for _ in range(count):
        sys.stdout.write("\033[F\033[K")
    sys.stdout.flush()
