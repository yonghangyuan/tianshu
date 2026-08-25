"""模型选择 — F4 状态栏内嵌菜单（↑↓ 导航 / Enter 确认 / Esc 取消）。

设计（2026-08-25，第一版独立浮层 Application 已废弃）：
  ptk 不允许嵌套运行 Application——F4 在主 PromptSession 的按键绑定里
  再跑一个 app.run() 必炸（coroutine 泄漏 / NoConsoleScreenBufferError）。
  改用 F2 闸门同款模式：菜单画进 bottom_toolbar（多行），↑↓/Enter/Esc
  以 filter 键绑定接管，输入缓冲 read_only 冻结，选完恢复。零嵌套、
  不进 alt-screen，scrollback 与原生复制不受影响。

ToolbarHandler 持菜单状态，本模块只提供纯渲染文本（可单测）。
"""

from __future__ import annotations

from typing import Any

MENU_TITLE = " 选择模型"


def render_menu_lines(
    options: list[dict[str, Any]],
    index: int,
    title: str = MENU_TITLE,
) -> str:
    """菜单多行文本（喂给 bottom_toolbar；\n 分行，ptk 原生支持多行）。

    Args:
        options: [{"value", "label", "desc"}]
        index: 当前高亮项。
    """
    lines = [f"{title}  ↑↓ 选择 · Enter 确认 · Esc 取消"]
    for i, opt in enumerate(options):
        cursor = "❯" if i == index else " "
        desc = opt.get("desc", "")
        lines.append(f" {cursor} {opt['label']}  {desc}")
    return "\n".join(lines)


def next_index(index: int, n: int, delta: int) -> int:
    """循环导航：越界回绕。"""
    return (index + delta) % n
