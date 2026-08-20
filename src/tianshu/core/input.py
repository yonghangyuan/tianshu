"""终端输入增强 — prompt_toolkit 可选依赖。

提供：
  - 会话历史（上下箭头）
  - Tab 补全
  - 多行输入（Alt+Enter 或 \\ 续行）

降级策略：如果 prompt_toolkit 未安装，fallback 到标准 input()。
"""

from __future__ import annotations

from typing import Any


def create_input_handler(
    history: list[str] | None = None,
    commands: list[str] | None = None,
    model_names: list[str] | None = None,
    mode_callback: Any = None,  # Callable[[], str] — 切换模式，返回新模式名
    preset_callback: Any = None,  # Callable — 无参读当前预设；传名应用（F2 闸门确认后）
    preset_gate: Any = True,     # 敏感预设（免确认类）F2 进入前弹 y/N 闸门
    fullscreen: bool = True,     # 尝试 inline 状态栏（bottom_toolbar；历史参数名保留）
    output_consoles: list | None = None,  # 兼容旧签名（inline 模式不接管输出）
    prompt_text: Any = None,     # 兼容旧签名（inline 提示符由 prompt() 传入）
    status_callback: Any = None,  # Callable[[], str] — 状态栏文本
) -> "InputHandler":
    """工厂函数：创建输入处理器。

    优先级：inline 状态栏（PromptSession + bottom_toolbar，打字时常驻
    模式/预设/token）→ PromptSession（无状态栏）→ 标准 input()。
    不可用时（无 TTY / 无 prompt_toolkit / Git Bash / 已在 async 循环）
    自动降级，行为与旧版一致。
    """
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            return FallbackHandler(history=history or [])
    except RuntimeError:
        pass

    completer = None
    pt_history = None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.key_binding import KeyBindings
        from pathlib import Path as _P

        # 构建补全词列表
        completions: list[str] = []
        if commands:
            completions.extend(c for c in commands)
        if model_names:
            completions.extend(model_names)

        # 自定义补全器: 默认用命令+模型补全, @ 触发文件路径补全
        from prompt_toolkit.completion import Completer, Completion

        class _SmartCompleter(Completer):
            def __init__(self, word_completer, cwd):
                self._word = word_completer
                self._cwd = cwd

            def get_completions(self, document, complete_event):
                yield from self._get(document, complete_event)

            async def get_completions_async(self, document, complete_event):
                for c in self._get(document, complete_event):
                    yield c

            def _get(self, document, complete_event):
                text = document.text_before_cursor
                # 检查是否在 @ 上下文中
                last_at = text.rfind("@")
                if last_at >= 0:
                    after_at = text[last_at:]
                    if " " not in after_at and "\n" not in after_at:
                        prefix = after_at[1:]
                        import os as _os
                        search_dir = self._cwd
                        if "/" in prefix or "\\" in prefix:
                            parent = _os.path.dirname(prefix)
                            if parent and _os.path.isdir(_os.path.join(str(self._cwd), parent)):
                                search_dir = _os.path.join(str(self._cwd), parent)
                                prefix = _os.path.basename(prefix)
                        try:
                            for entry in sorted(_os.listdir(search_dir)):
                                if entry.startswith(prefix) and not entry.startswith("."):
                                    full = _os.path.join(search_dir, entry)
                                    display = f"@{entry}{'/' if _os.path.isdir(full) else ''}"
                                    yield Completion(
                                        entry + ("/" if _os.path.isdir(full) else ""),
                                        start_position=-len(prefix),
                                        display=display,
                                    )
                        except OSError:
                            pass
                        return
                if self._word:
                    yield from self._word.get_completions(document, complete_event)

        word_comp = WordCompleter(completions, ignore_case=True, sentence=True) if completions else None
        completer = _SmartCompleter(word_comp, _P.cwd())

        # 持久化历史文件
        _hist_path = _P.home() / ".tianshu" / "cli_history.txt"
        _hist_path.parent.mkdir(parents=True, exist_ok=True)
        pt_history = FileHistory(str(_hist_path))
        if history:
            for h in history:
                pt_history.append_string(h)

        # ── inline 状态栏（bottom_toolbar，打字时常驻）──
        if fullscreen:
            try:
                from tianshu.gateway.statusbar import create_toolbar_handler
                handler = create_toolbar_handler(
                    completer=completer,
                    history=pt_history,
                    mode_callback=mode_callback,
                    preset_callback=preset_callback,
                    preset_gate=preset_gate,
                    status_callback=status_callback,
                )
                if handler is not None:
                    return handler
            except Exception:
                pass  # 状态栏失败 → 裸 PromptSession

        session = PromptSession(history=pt_history)

        return PromptToolkitHandler(
            session=session,
            history=pt_history,
            completer=completer,
            mode_callback=mode_callback,
            preset_callback=preset_callback,
        )
    except ImportError:
        pass
    except Exception:
        pass  # prompt_toolkit 其他异常（Git Bash NoConsoleScreenBufferError 等）

    return FallbackHandler(history=history or [])


class InputHandler:
    """输入处理器接口。"""

    def prompt(self, prompt_text: str = "▸ ") -> str:
        raise NotImplementedError

    def add_to_history(self, text: str) -> None:
        raise NotImplementedError

    @property
    def multiline_supported(self) -> bool:
        return False


class FallbackHandler(InputHandler):
    """标准 input() 降级实现。"""

    def __init__(self, history: list[str] | None = None) -> None:
        self._history: list[str] = history or []

    def prompt(self, prompt_text: str = "▸ ") -> str:
        return input(prompt_text)

    def add_to_history(self, text: str) -> None:
        self._history.append(text)

    @property
    def multiline_supported(self) -> bool:
        return False


class PromptToolkitHandler(InputHandler):
    """prompt_toolkit 完整实现。支持 Shift+Tab 切模式。"""

    def __init__(self, session, history, completer=None, mode_callback=None,
                 preset_callback=None) -> None:
        self._session = session
        self._history = history
        self._completer = completer
        self._mode_callback = mode_callback
        self._preset_callback = preset_callback

    def prompt(self, prompt_text: str = "▸ ") -> str:
        from prompt_toolkit.styles import Style
        from prompt_toolkit.key_binding import KeyBindings

        style = Style.from_dict({
            "prompt": "bold #60a5fa",
        })
        kb = KeyBindings()

        # ── Shift+Tab: 切换模式 ──
        @kb.add("s-tab")
        def _(event):
            if self._mode_callback:
                self._mode_callback()
            # 刷新提示符——通过触发一个空操作让 session 重新渲染
            event.app.renderer.clear()
            event.app.invalidate()

        # ── F2: 切换预设 (standard / minimal / code) ──
        @kb.add("f2")
        def _(event):
            if self._preset_callback:
                self._preset_callback()
            event.app.renderer.clear()
            event.app.invalidate()

        # ── Alt+Enter: 插入换行 ──
        @kb.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        # ── Ctrl+R: 历史搜索 ──
        @kb.add("c-r")
        def _(event):
            event.app.layout.focus(event.app.layout.current_control.search_buffer_control)

        # ── Enter: 提交（末尾 \\ 续行）──
        @kb.add("enter")
        def _(event):
            buffer = event.current_buffer
            text = buffer.text
            if text.rstrip().endswith("\\"):
                buffer.text = text.rstrip()[:-1] + "\n"
                buffer.cursor_position = len(buffer.text)
            else:
                buffer.validate_and_handle()

        try:
            text = self._session.prompt(
                [("class:prompt", prompt_text)],
                style=style,
                completer=self._completer,
                key_bindings=kb,
                multiline=True,
            )
            return text
        except Exception:
            return self._session.prompt(
                [("class:prompt", prompt_text)],
                style=style,
                completer=self._completer,
            )

    def add_to_history(self, text: str) -> None:
        # prompt_toolkit 内部自动管理 history
        pass

    @property
    def multiline_supported(self) -> bool:
        return True
