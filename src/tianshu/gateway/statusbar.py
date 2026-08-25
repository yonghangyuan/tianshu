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
    model: str = "",
) -> str:
    """状态栏单行文本：模式 预设 模型 | tok 累计 (缓存命中) | 耗时。"""
    m = _MODE_SYMBOLS.get(mode, mode)
    p = _PRESET_SYMBOLS.get(preset, preset)
    head = f" {m} {mode} · {p} {preset}"
    if model:
        head += f" · {model}"
    parts = [head]
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
        preset_gate: Callable[[str, Callable[[bool], None]], None] | None = None,
        model_menu: Callable[[], list[dict]] | None = None,
        model_callback: Callable[[str], None] | None = None,
    ) -> None:
        """preset_gate(target, apply): 进敏感预设（minimal）前弹 y/N 确认。

        在 F2 按键绑定内同步调用——inline 模式下不能等主循环（用户还
        在 prompt 里打字）。apply(True/False) 回调应用/取消。

        model_menu(): 返回模型菜单项 [{"value", "label", "desc"}]（F4 弹出）。
        model_callback(value): F4 菜单选中后回调（会话级切换，不落盘）。
        """
        self._session = session
        self._completer = completer
        self._mode_callback = mode_callback
        self._preset_callback = preset_callback
        self._status_callback = status_callback
        self._preset_gate = preset_gate
        self._model_menu = model_menu
        self._model_callback = model_callback
        self.status_override = ""  # 回合中 spinner 文本 / 闸门提示（非空顶替状态栏）
        self._gate_active = False
        self._gate_target = ""
        # F4 模型菜单状态（active 期间 ↑↓/Enter/Esc 由菜单接管）
        self._menu_active = False
        self._menu_options: list[dict] = []
        self._menu_index = 0

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
            self._cycle_preset(event)
            # 纯按键（输入缓冲无变化）不会触发重绘——强制重算 toolbar
            event.app.renderer.clear()
            event.app.invalidate()

        # ── F4: 模型选择菜单（状态栏内嵌，↑↓/Enter/Esc）──
        @kb.add("f4")
        def _(event):
            self._open_model_menu(event)

        # ── ↑↓/Enter/Esc/q: F4 菜单激活时导航（filter 接管）──
        @kb.add("up", filter=self._menu_filter)
        def _(event):
            self._menu_navigate(-1, event)
            event.app.invalidate()  # 高亮行变化重绘

        @kb.add("down", filter=self._menu_filter)
        def _(event):
            self._menu_navigate(1, event)
            event.app.invalidate()

        @kb.add("enter", filter=self._menu_filter)
        def _(event):
            self._menu_confirm(event)

        @kb.add("escape", filter=self._menu_filter)
        def _(event):
            self._menu_cancel(event)

        @kb.add("q", filter=self._menu_filter)
        def _(event):
            self._menu_cancel(event)

        # ── y/n: preset_gate 弹窗中确认/取消 ──
        @kb.add("y", filter=self._gate_filter)
        def _(event):
            self._gate_answer(True)

        @kb.add("n", filter=self._gate_filter)
        def _(event):
            self._gate_answer(False)

        @kb.add("escape", "c-c", filter=self._gate_filter)
        def _(event):
            self._gate_answer(False)

        # ── Alt+Enter: 插入换行 ──
        @kb.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        # ── Ctrl+R: 历史搜索 ──
        @kb.add("c-r")
        def _(event):
            event.app.layout.focus(event.app.layout.current_control.search_buffer_control)

        # ── Enter: 提交（末尾 \ 续行；菜单/闸门激活时让位）──
        from prompt_toolkit.filters import Condition as _Cond

        @kb.add("enter", filter=_Cond(lambda: not self._menu_active))
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

    # ── F4 模型选择菜单（状态栏内嵌，同 F2 闸门模式——零嵌套）──────────

    def _open_model_menu(self, event) -> None:
        """F4：拉菜单项，冻结输入缓冲，菜单画进 bottom_toolbar。"""
        if self._menu_active or self._gate_active:
            return  # 菜单/闸门已开时不重入
        if self._model_menu is None:
            return
        try:
            options = self._model_menu()
        except Exception:
            return
        if not options:
            return
        self._menu_options = options
        # 预高亮当前模型（main 在菜单项里标 cur 键）
        self._menu_index = next(
            (i for i, o in enumerate(options) if o.get("cur")), 0
        )
        self._menu_active = True
        app = getattr(self._session, "app", None) or getattr(event, "app", None)
        if app is not None:
            try:
                # read_only 必须是可调用（ptk 内置 filter 会调 read_only()），
                # 裸 bool 会炸 'bool' object is not callable——同 F2 闸门写法
                app.current_buffer.read_only = lambda: True
            except Exception:
                pass

    def _menu_navigate(self, delta: int, event) -> None:
        from tianshu.gateway.model_picker import next_index
        self._menu_index = next_index(self._menu_index, len(self._menu_options), delta)

    def _menu_confirm(self, event) -> None:
        chosen = self._menu_options[self._menu_index]["value"]
        self._menu_close(event)
        if self._model_callback is not None:
            self._model_callback(chosen)

    def _menu_cancel(self, event) -> None:
        self._menu_close(event)

    def _menu_close(self, event) -> None:
        """收菜单：解冻输入缓冲，状态回归常规状态栏，强制重绘。"""
        self._menu_active = False
        self._menu_options = []
        self._menu_index = 0
        app = getattr(self._session, "app", None) or getattr(event, "app", None)
        if app is not None:
            try:
                app.current_buffer.read_only = lambda: False
            except Exception:
                pass
            try:
                # 收菜单必须强制重绘：纯状态翻转不触发 ptk 重算 toolbar，
                # 菜单文本会残留在屏上（用户按 Enter/Esc"界面不退"的根因）
                app.renderer.clear()
                app.invalidate()
            except Exception:
                pass

    @property
    def _menu_filter(self):
        """菜单激活时才吃 ↑↓/Enter/Esc/q。"""
        from prompt_toolkit.filters import Condition
        return Condition(lambda: self._menu_active)

    def _menu_text(self) -> str:
        """菜单多行文本（_toolbar_text 在菜单激活时改喂此文本）。"""
        from tianshu.gateway.model_picker import render_menu_lines
        return render_menu_lines(self._menu_options, self._menu_index)

    # ── F2 预设循环 + minimal 进入闸门 ─────────────────────────────

    # ── F2 预设循环 + minimal 进入闸门 ─────────────────────────────

    def _cycle_preset(self, event) -> None:
        """F2 按键处理：下一预设；目标为敏感预设时先弹 y/N 闸门。"""
        if self._gate_active:
            return  # 闸门开着时 F2 不响应（先答 y/n）
        from tianshu.core.presets import cycle_preset, get_preset
        current = self._preset_callback() if self._preset_callback else "standard"
        nxt = cycle_preset(current)
        target = get_preset(nxt)
        if target.skip_confirm and self._preset_gate is not None:
            # 敏感预设（免确认类）→ 闸门 y/N：提示进状态栏，输入缓冲冻结
            n_tools = len(target.allowlist) if target.allowlist else "全部"
            self._gate_active = True
            self._gate_target = nxt
            self.status_override = f" ⚠ {target.label}: 仅 {n_tools} 工具且免确认，进入? [y/N]"
            event.app.current_buffer.read_only = lambda: True  # type: ignore[assignment]
        else:
            self._apply_preset(nxt)

    def _gate_answer(self, yes: bool) -> None:
        """闸门应答：y 应用目标预设，n/Esc 取消。"""
        if not self._gate_active:
            return
        self._gate_active = False
        target, self._gate_target = self._gate_target, ""
        app = getattr(self._session, "app", None)
        if app is not None:
            app.current_buffer.read_only = lambda: False  # type: ignore[assignment]
        self.status_override = ""
        if yes:
            self._apply_preset(target)

    def _apply_preset(self, name: str) -> None:
        """应用预设并通知外部（preset_callback 双向：传参=设定）。"""
        self.status_override = ""
        if self._preset_callback is not None:
            try:
                self._preset_callback(name)
            except TypeError:
                self._preset_callback()  # 旧签名兼容

    @property
    def _gate_filter(self):
        """闸门激活时才吃 y/n/Esc 键。"""
        from prompt_toolkit.filters import Condition
        return Condition(lambda: self._gate_active)

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
        # F4 菜单激活 → 菜单文本顶替（优先于 spinner/闸门，二者不并存）
        if self._menu_active:
            return [("class:toolbar", self._menu_text())]
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
    preset_gate: Any = True,
    status_callback: Callable[[], str] | None = None,
    model_menu: Callable[[], list[dict]] | None = None,
    model_callback: Callable[[str], None] | None = None,
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
            preset_gate=preset_gate,
            status_callback=status_callback,
            model_menu=model_menu,
            model_callback=model_callback,
        )
    except Exception:
        return None  # Git Bash NoConsoleScreenBufferError 等
