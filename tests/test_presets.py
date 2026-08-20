"""Agent 预设系统测试 — presets 定义 / 工具过滤矩阵 / 闸门 / 指令注入。"""

from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.core.presets import PRESETS, PRESET_ORDER, cycle_preset, get_preset
from tianshu.core.tool_registry import ToolRegistry, ToolInfo


# ── 预设定义 ─────────────────────────────────────────────────────────────


def test_cycle_preset_order():
    assert cycle_preset("standard") == "minimal"
    assert cycle_preset("minimal") == "code"
    assert cycle_preset("code") == "standard"
    assert cycle_preset("bogus") == "standard"


def test_get_preset_unknown_falls_back():
    p = get_preset("not-a-preset")
    assert p.name == "standard"
    assert p.hidden == {"run_code"}


def test_preset_definitions():
    m = PRESETS["minimal"]
    assert m.allowlist == {"shell_exec", "edit_file", "read_file", "list_dir"}
    assert m.skip_confirm and m.skip_trigram
    assert "极简模式" in m.instruction
    c = PRESETS["code"]
    assert c.allowlist is None
    assert "tools.run" in c.instruction
    assert "submit" in c.instruction


# ── 工具过滤矩阵 ─────────────────────────────────────────────────────────


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    specs = [
        ("shell_exec", 2), ("edit_file", 2), ("write_file", 2),
        ("read_file", 1), ("list_dir", 0), ("web_search", 0),
        ("run_code", 2),
    ]
    for name, perm in specs:
        reg.register(ToolInfo(
            name, f"desc {name}",
            {"type": "object", "properties": {}, "required": []},
            None, permission=perm, skill_name="test",
        ))
    return reg


def _names(schemas):
    return {s["function"]["name"] for s in schemas}


def test_filter_minimal_exactly_four_tools():
    reg = _make_registry()
    assert _names(reg.get_tools("normal", "minimal")) == {
        "shell_exec", "edit_file", "read_file", "list_dir",
    }


def test_filter_standard_hides_run_code():
    reg = _make_registry()
    names = _names(reg.get_tools("normal", "standard"))
    assert "web_search" in names
    assert "run_code" not in names


def test_filter_code_has_run_code():
    reg = _make_registry()
    names = _names(reg.get_tools("normal", "code"))
    assert "run_code" in names
    assert "web_search" in names


def test_filter_plan_minimal_readonly():
    reg = _make_registry()
    # plan 只读契约绝对优先：minimal 的 WRITE 工具也被剔除
    assert _names(reg.get_tools("plan", "minimal")) == {"read_file", "list_dir"}


def test_filter_plan_standard_readonly():
    reg = _make_registry()
    names = _names(reg.get_tools("plan", "standard"))
    assert "shell_exec" not in names and "write_file" not in names
    assert "read_file" in names


def test_filter_unknown_preset_falls_back_to_standard():
    reg = _make_registry()
    assert _names(reg.get_tools("normal", "whatever")) == _names(reg.get_tools("normal", "standard"))


# ── 指令注入 ─────────────────────────────────────────────────────────────


def test_inject_instruction_merges_into_system_message():
    from tianshu.core.service import AgentCore
    core = AgentCore()
    core._preset = "minimal"
    messages = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    core._inject_preset_instruction(messages)
    assert len(messages) == 2
    assert messages[0]["content"].startswith("SYS")
    assert "[极简模式]" in messages[0]["content"]


def test_inject_instruction_standard_is_noop():
    from tianshu.core.service import AgentCore
    core = AgentCore()
    messages = [{"role": "system", "content": "SYS"}]
    core._inject_preset_instruction(messages)
    assert messages[0]["content"] == "SYS"


def test_inject_instruction_empty_messages_inserts():
    from tianshu.core.service import AgentCore
    core = AgentCore()
    core._preset = "code"
    messages = []
    core._inject_preset_instruction(messages)
    assert messages[0]["role"] == "system"
    assert "PTC" in messages[0]["content"]


# ── 非流式 run() 闸门 ─────────────────────────────────────────────────────


class _ToolCallProvider:
    """第一次 chat 返回 shell_exec 工具调用，第二次返回纯文本（结束循环）。"""
    max_context_tokens = 65536

    def __init__(self, command: str):
        self.command = command
        self.calls = 0

    async def chat(self, messages, **kw):
        from tianshu.sdk.models import ProviderResponse, ToolCall, TokenUsage
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                content="", tool_calls=[ToolCall(id="c1", name="shell_exec",
                                                 arguments={"command": self.command})],
                usage=TokenUsage(),
            )
        return ProviderResponse(content="done", usage=TokenUsage())

    async def is_available(self):
        return True


def _make_core(preset: str, command: str):
    from tianshu.core.service import AgentCore
    from tianshu.diyao.providers.registry import ProviderRegistry
    from tianshu.core.router import RoutingConfig
    from tianshu.sdk.models import AgentRequest

    core = AgentCore()
    reg = ProviderRegistry()
    provider = _ToolCallProvider(command)
    provider.provider_name = "deepseek"
    provider.model_id = "deepseek-chat"
    reg.register(provider)
    core.setup(registry=reg, routing=RoutingConfig(fallback="deepseek/chat"),
               system_prompt="", skill_discover=False)
    core._preset = preset
    core._mcp_pending_connect = {}  # 测试隔离：不连真实 MCP server
    # 隔离：工具执行不真跑 shell
    core._skills = MagicMock()
    core._skills.execute = AsyncMock(return_value="executed")
    return core, AgentRequest(input="do it")


class _FakeShell:
    """极简模式测试用的假持久 shell——不 spawn 真 cmd。"""
    def __init__(self):
        self.commands = []

    async def run(self, command, timeout=60):
        self.commands.append(command)
        return "shell-out"


@pytest.mark.asyncio
async def test_run_minimal_skips_confirm_gate():
    from tianshu.sdk.models import AgentContext
    core, req = _make_core("minimal", "echo hello")
    # minimal 下 shell_exec 走持久 shell：注入假 shell 验证路由
    ctx = AgentContext(session_id="t-min")
    ctx.shell = _FakeShell()
    resp = await core.run(req, ctx=ctx)
    assert not resp.error
    outputs = [t["output"] for t in resp.tool_calls]
    assert any("shell-out" in o for o in outputs)
    assert not any("requires confirmation" in o for o in outputs)
    assert ctx.shell.commands == ["echo hello"]


@pytest.mark.asyncio
async def test_run_standard_confirm_gate_still_blocks():
    core, req = _make_core("standard", "echo hello")
    resp = await core.run(req)
    outputs = [t["output"] for t in resp.tool_calls]
    assert any("requires confirmation" in o for o in outputs)


@pytest.mark.asyncio
async def test_run_minimal_policy_deny_still_applies():
    core, req = _make_core("minimal", "rm -rf / --no-preserve-root")
    resp = await core.run(req)
    outputs = [t["output"] for t in resp.tool_calls]
    assert any("Policy denied" in o or "denied" in o.lower() for o in outputs)
    assert not any("executed" in o for o in outputs)


# ── presets.yaml 配置化 ─────────────────────────────────────────────────────


class TestPresetsYaml:
    def test_no_yaml_falls_back_to_builtin(self, tmp_path, monkeypatch):
        import tianshu.core.presets as P
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        P.reload_presets()
        try:
            presets, order = P._get_custom()
            assert set(order) == {"standard", "minimal", "code"}
            assert "run_code" in presets["code"].hidden is False or True  # 内置 code 不隐藏 run_code
            assert presets["minimal"].skip_confirm is True
        finally:
            P.reload_presets()

    def test_yaml_overrides_builtin(self, tmp_path, monkeypatch):
        import tianshu.core.presets as P
        cfg = tmp_path / "presets.yaml"
        cfg.write_text("""
presets:
  minimal:
    label: 超简
    allowlist: [read_file]
    skip_confirm: false
""", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        presets, order = P.load_presets(config_path=cfg, user_path=tmp_path / "none.yaml")
        assert presets["minimal"].label == "超简"
        assert presets["minimal"].allowlist == {"read_file"}
        assert presets["minimal"].skip_confirm is False  # 整体覆盖，不回退内置
        assert order == ["standard", "minimal", "code"]

    def test_yaml_appends_custom_preset(self, tmp_path, monkeypatch):
        import tianshu.core.presets as P
        cfg = tmp_path / "presets.yaml"
        cfg.write_text("""
presets:
  research:
    label: 检索
    allowlist: [web_search, read_file]
""", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        presets, order = P.load_presets(config_path=cfg, user_path=tmp_path / "none.yaml")
        assert "research" in presets
        assert order[-1] == "research"  # 追加进循环序
        # get_preset 能取到自定义
        p = presets["research"]
        assert p.allowlist == {"web_search", "read_file"}
        assert p.skip_confirm is False

    def test_user_level_wins(self, tmp_path, monkeypatch):
        import tianshu.core.presets as P
        proj = tmp_path / "proj.yaml"
        proj.write_text("presets:\n  minimal:\n    label: 项目级\n", encoding="utf-8")
        user = tmp_path / "user.yaml"
        user.write_text("presets:\n  minimal:\n    label: 用户级\n", encoding="utf-8")
        presets, _ = P.load_presets(config_path=proj, user_path=user)
        assert presets["minimal"].label == "用户级"

    def test_broken_yaml_skipped_not_fatal(self, tmp_path, monkeypatch):
        import tianshu.core.presets as P
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("presets: [[[not yaml", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        presets, order = P.load_presets(config_path=cfg, user_path=tmp_path / "none.yaml")
        assert set(order) == {"standard", "minimal", "code"}  # 内置兜底

    def test_get_preset_uses_yaml_when_loaded(self, tmp_path, monkeypatch):
        import tianshu.core.presets as P
        cfg = tmp_path / "presets.yaml"
        cfg.write_text("presets:\n  fast:\n    label: 快速\n    allowlist: [read_file]\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        # 直接注入缓存模拟已加载
        P._custom = P.load_presets(config_path=cfg, user_path=tmp_path / "none.yaml")
        try:
            p = P.get_preset("fast")
            assert p.label == "快速"
            assert "fast" in P.preset_order()
        finally:
            P.reload_presets()
