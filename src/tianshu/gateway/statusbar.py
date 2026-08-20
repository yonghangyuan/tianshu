"""inline 底部状态栏 — prompt_toolkit bottom_toolbar（mycli/pgcli 同款）。

设计（2026-08-20 用户拍板，替代 alt-screen 全屏方案）：
  - 不进 alt-screen：对话照常往下滚，终端原生 scrollback/滚轮/选字复制
    全部自然工作——alt-screen 接管控制台后 Windows 下「滚轮事件」与
    「QUICK_EDIT 选字」互斥，选字复制不可能兼得，此路线已废弃。
  - 只在用户打字时底部常驻一条状态栏（bottom_toolbar）：模式/预设/
    token/缓存命中（format_status_bar），或回合中 rich spinner 的文本。
  - 回合进行中 PromptSession 不在前台 → Ctrl+C 回归原生
    KeyboardInterrupt（asyncio.run 层 except 已有），取消竞速机制退役。

format_status_bar 从原 fullscreen.py 迁来（纯函数，零依赖）。
"""

from __future__ import annotations

from typing import Any, Callable

# ═══════════════════════════════════════════════════════════════════════════
# 状态栏文本（纯函数）
# ═══════════════════════════════════════════════════════════════════════════

_MODE_SYMBOLS = {"normal": "⏵", "auto": "⏵⏵", "plan": "◉"}
_PRESET_SYMBOLS = {"standard": "◇", "minimal": "◆", "code": "✧"}


def _fmt_k(n: int) -> str:
    """千分位缩写：12345 → 12.3k。"""
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def format_status_bar(
    mode: str,
    preset: str,
    prompt_tok: int,
    completion_tok: int,
    cached_tok: int = 0,
    elapsed: float = 0.0,
) -> str:
    """状态栏单行文本：模式 预设 | tok 累计 (缓存命中) | 耗时。"""
    m = _MODE_SYMBOLS.get(mode, mode)
    p = _PRESET_SYMBOLS.get(preset, preset)
    parts = [f" {m} {mode} · {p} {preset}"]
    tok = f"↑{_fmt_k(prompt_tok)} ↓{_fmt_k(completion_tok)}"
    if cached_tok > 0 and prompt_tok > 0:
        tok += f" ⚡{_fmt_k(cached_tok)} ({cached_tok * 100 // prompt_tok}%)"
    parts.append(tok)
    if elapsed > 0:
        parts.append(f"{elapsed:.1f}s")
    return "  │  ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# 输入处理器
# ═══════════════════════════════════════════════════════════════════════════

class ToolbarHandler:
    """PromptSession + bottom_toolbar——打字时常驻状态栏，输出区不受接管。

    与 InputHandler 接口对齐（prompt/add_to_history/ask_yn/close），
    另提供 set_status(text)（回合中 spinner 文本顶替状态栏）。
    """

    def __init__(
        self,
        session: Any,
        completer: Any = None,
        mode_callback: Callable | None = None,
        preset_callback: Callable | None = None,
        status_callback: Callable[[], str] | None = None,
    ) -> None:
        self._session = session
        self._completer = completer
        self._mode_callback = mode_callback
        self._preset_callback = preset_callback
        self._status_callback = status_callback
        self.status_override = ""  # 回合中 spinner 文本（非空时顶替状态栏）

    # ── InputHandler 接口 ────────────────────────────────────────────

    def prompt(self, prompt_text: str = "▸ ") -> str:
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style

        style = Style.from_dict({
            "prompt": "bold #60a5fa",
            "toolbar": "reverse bold #2563eb",
        })
        kb = KeyBindings()

        # ── Shift+Tab: 切换模式 ──
        @kb.add("s-tab")
        def _(event):
            if self._mode_callback:
                self._mode_callback()
            event.app.renderer.clear()
            event.app.invalidate()

        # ── F2: 切换预设 (standard/minimal/code) ──
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

        # ── Enter: 提交（末尾 \ 续行）──
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
            return self._session.prompt(
                [("class:prompt", prompt_text)],
                style=style,
                completer=self._completer,
                key_bindings=kb,
                multiline=True,
                bottom_toolbar=self._toolbar_text,
            )
        except Exception:
            # 个别环境下 multiline/key_bindings 组合异常 → 最小配置重试
            return self._session.prompt(
                [("class:prompt", prompt_text)],
                style=style,
                completer=self._completer,
            )

    def add_to_history(self, text: str) -> None:
        pass  # PromptSession history 自动管理

    def ask_yn(self, text: str) -> bool | None:
        try:
            ans = self._session.prompt(f"  {text} [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        return ans in ("y", "yes")

    def close(self) -> None:
        pass  # inline 模式不接管终端，无需恢复

    @property
    def multiline_supported(self) -> bool:
        return True

    # ── 回合中状态顶替（_StatusLine 用）────────────────────────────

    def set_status(self, text: str) -> None:
        """设置/清除状态栏顶替文本（回合中 spinner；"" = 恢复常规状态栏）。

        只在 prompt() 活跃期间有视觉效果——回合进行时 PromptSession 不在
        前台，文本由 rich spinner 呈现；此处存值供下一帧 toolbar 使用。
        """
        self.status_override = text

    # ── 内部 ─────────────────────────────────────────────────────────

    def _toolbar_text(self):
        text = self.status_override
        if not text and self._status_callback is not None:
            try:
                text = self._status_callback()
            except Exception:
                text = ""  # 回调异常绝不让输入挂掉
        if not text:
            return ""  # 空文本 → ptk 不渲染 toolbar
        return [("class:toolbar", text)]


# ═══════════════════════════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════════════════════════

def create_toolbar_handler(
    *,
    completer: Any = None,
    history: Any = None,
    mode_callback: Callable | None = None,
    preset_callback: Callable | None = None,
    status_callback: Callable[[], str] | None = None,
) -> "ToolbarHandler | None":
    """创建 inline 状态栏处理器；不可用（无 TTY/Git Bash/无 ptk）返回 None。"""
    if not hasattr(__import__("sys"), "stdin") or not __import__("sys").stdin.isatty():
        return None
    try:
        from prompt_toolkit import PromptSession
        session = PromptSession(history=history)
        return ToolbarHandler(
            session=session,
            completer=completer,
            mode_callback=mode_callback,
            preset_callback=preset_callback,
            status_callback=status_callback,
        )
    except Exception:
        return None  # Git Bash NoConsoleScreenBufferError 等
