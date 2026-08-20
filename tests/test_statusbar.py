"""inline 底部状态栏测试 — format_status_bar 纯函数 + ToolbarHandler。"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root / "src"))

from tianshu.gateway.statusbar import (
    ToolbarHandler,
    create_toolbar_handler,
    format_status_bar,
)


# ── format_status_bar（纯函数）─────────────────────────────────────────────


def test_status_bar_normal():
    s = format_status_bar("normal", "standard", 1234, 567)
    assert "normal" in s and "standard" in s
    assert "1.2k" in s and "567" in s


def test_status_bar_symbols_and_k():
    s = format_status_bar("auto", "minimal", 1500000, 2500000)
    assert "⏵⏵" in s and "◆" in s
    assert "1500.0k" in s


def test_status_bar_small_tokens():
    s = format_status_bar("plan", "code", 0, 0)
    assert "↑0" in s and "↓0" in s


def test_status_bar_cached_percent():
    s = format_status_bar("normal", "standard", 1000, 100, cached_tok=600)
    assert "⚡600 (60%)" in s


def test_status_bar_no_cached_when_zero():
    s = format_status_bar("normal", "standard", 1000, 100, cached_tok=0)
    assert "⚡" not in s


def test_status_bar_elapsed():
    s = format_status_bar("normal", "standard", 10, 10, elapsed=2.5)
    assert "2.5s" in s


# ── ToolbarHandler（无 TTY 也可构造核心逻辑）──────────────────────────────


class _FakeSession:
    """记录 prompt() 调用参数的假 session。"""

    def __init__(self):
        self.calls: list[dict] = []

    def prompt(self, text, **kw):
        self.calls.append({"text": text, **kw})
        return "fake-input"


def _make_handler(**kw):
    session = _FakeSession()
    handler = ToolbarHandler(session=session, **kw)
    return handler, session


def test_handler_prompt_passes_toolbar():
    handler, session = _make_handler(status_callback=lambda: "状态栏文本")
    out = handler.prompt("▸ ")
    assert out == "fake-input"
    call = session.calls[-1]
    assert "bottom_toolbar" in call
    # toolbar 回调产出带样式片段
    fragments = call["bottom_toolbar"]()
    assert fragments == [("class:toolbar", "状态栏文本")]


def test_handler_toolbar_override_wins():
    handler, session = _make_handler(status_callback=lambda: "常规状态")
    handler.set_status("⠋ Thinking...")
    fragments = handler._toolbar_text()
    assert fragments == [("class:toolbar", "⠋ Thinking...")]


def test_handler_toolbar_empty_when_no_callback():
    handler, _ = _make_handler()
    assert handler._toolbar_text() == ""


def test_handler_toolbar_callback_exception_swallowed():
    def _boom():
        raise RuntimeError("竞态脏数据")

    handler, _ = _make_handler(status_callback=_boom)
    assert handler._toolbar_text() == ""  # 异常绝不让输入挂掉


def test_handler_interface():
    handler, _ = _make_handler()
    for m in ("prompt", "add_to_history", "ask_yn", "close", "set_status"):
        assert hasattr(handler, m)
    assert handler.multiline_supported is True


# ── 工厂降级链 ────────────────────────────────────────────────────────────


def test_create_toolbar_handler_none_when_not_tty(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert create_toolbar_handler() is None


def test_create_toolbar_handler_none_when_ptk_missing(monkeypatch):
    import builtins
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    real_import = builtins.__import__

    def _fake_import(name, *a, **kw):
        if name.startswith("prompt_toolkit"):
            raise ImportError("no prompt_toolkit")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert create_toolbar_handler() is None


# ── F2 预设闸门（敏感预设进入确认）─────────────────────────────────────────


class _FakeBuffer:
    read_only = False


class _FakeApp:
    current_buffer = _FakeBuffer()


class _FakeEvent:
    app = _FakeApp()


class TestPresetGate:
    def _handler(self):
        from tianshu.core.presets import reload_presets
        reload_presets()
        state = {"preset": "standard", "applied": []}

        def cb(p=None):
            if p is not None:
                state["applied"].append(p)
                state["preset"] = p
            return state["preset"]

        h = ToolbarHandler(session=None, preset_callback=cb, preset_gate=True)
        return h, state

    def test_gate_activates_for_minimal(self):
        h, state = self._handler()
        h._cycle_preset(_FakeEvent())
        assert h._gate_active is True
        assert "进入" in h.status_override
        assert state["preset"] == "standard"  # 未应用

    def test_gate_yes_applies(self):
        h, state = self._handler()
        h._cycle_preset(_FakeEvent())
        h._gate_answer(True)
        assert state["applied"] == ["minimal"]
        assert h._gate_active is False
        assert h.status_override == ""

    def test_gate_no_cancels(self):
        h, state = self._handler()
        h._cycle_preset(_FakeEvent())
        h._gate_answer(False)
        assert state["applied"] == []
        assert state["preset"] == "standard"

    def test_code_switches_directly(self):
        h, state = self._handler()
        state["preset"] = "minimal"
        h._cycle_preset(_FakeEvent())
        assert state["applied"] == ["code"]  # 免闸门直接切
        assert h._gate_active is False

    def test_gate_blocks_f2_while_active(self):
        h, _ = self._handler()
        h._cycle_preset(_FakeEvent())       # 闸门开
        applied_before = h._gate_target
        h._cycle_preset(_FakeEvent())       # 再按 F2 → 忽略
        assert h._gate_target == applied_before
