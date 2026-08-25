"""模型选择浮层 — prompt_toolkit Application，inline 渲染（不进 alt-screen）。

F4 弹出：↑↓ 导航 / Enter 确认 / Esc 或 q 取消。Claude Code 式选择体验，
画在光标下方，退出即散，对话 scrollback 与终端原生复制不受影响。
"""

from __future__ import annotations

from typing import Any


def pick_from_list(
    options: list[dict[str, Any]],
    title: str = "选择",
) -> str | None:
    """弹出选择浮层，返回选中项的 value；取消返回 None。

    Args:
        options: [{"value": "ollama/llama3.2", "label": "...", "desc": "..."}]
        title: 浮层标题。
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    index = 0
    n = len(options)

    def _accepted(i: int) -> None:
        nonlocal selected
        selected = options[i]["value"]
        app.exit(result=selected)

    def _cancelled() -> None:
        app.exit(result=None)

    selected: str | None = None

    def _body_text():
        lines = []
        for i, opt in enumerate(options):
            cursor = "❯ " if i == index else "  "
            if i == index:
                lines.append(("class:selected", f"{cursor}{opt['label']}  {opt.get('desc', '')}\n"))
            else:
                lines.append(("class:normal", f"{cursor}{opt['label']}  "))
                if opt.get("desc"):
                    lines.append(("class:dim", f"{opt['desc']}\n"))
                else:
                    lines.append(("", "\n"))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        nonlocal index
        index = (index - 1) % n

    @kb.add("down")
    def _(event):
        nonlocal index
        index = (index + 1) % n

    @kb.add("enter")
    def _(event):
        _accepted(index)

    @kb.add("escape")
    def _(event):
        _cancelled()

    @kb.add("q")
    def _(event):
        _cancelled()

    style = Style.from_dict({
        "title": "bold cyan",
        "selected": "bold fg:#60a5fa",
        "normal": "",
        "dim": "fg:#6b7280",
        "hint": "fg:#6b7280",
    })

    try:
        layout = Layout(HSplit([
            Window(
                FormattedTextControl([("class:title", f" {title}\n")]),
                height=1,
                dont_extend_height=True,
            ),
            Window(
                FormattedTextControl(_body_text),
                dont_extend_height=True,
            ),
            Window(
                FormattedTextControl([("class:hint", " ↑↓ 选择  Enter 确认  Esc/q 取消")]),
                height=1,
                dont_extend_height=True,
            ),
        ]))

        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,  # inline：画在光标下方，不接管屏幕
        )
        return app.run()
    except Exception:
        # 无 TTY / Git Bash NoConsoleScreenBufferError / ptk 环境异常 → 取消
        return None
