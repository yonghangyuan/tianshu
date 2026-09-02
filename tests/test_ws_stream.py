"""WS 群聊流式广播测试 — _run_agent_stream 逐 token 协议。

2026-09-02 chat.html 流式翻新的服务端契约：
typing → (reasoning/chat_delta/tool)* → chat(完整+id 落库)。
mock AgentCore.run_stream 产出事件序列，断言广播顺序与落库内容。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

import tianshu.gateway.server as srv
from tianshu.sdk.models import (
    AgentRequest, AgentContext,
    ContentDelta, ReasoningDelta, ToolCallStart, ToolCallResult, StreamDone,
)


@dataclass
class _FakeCore:
    """最小 AgentCore 替身——只实现 run_stream。"""
    _mode: str = "normal"
    _automode: bool = False
    events: list = field(default_factory=list)
    captured_ctx: AgentContext | None = None

    async def run_stream(self, request: AgentRequest, ctx=None):
        self.captured_ctx = ctx
        for ev in self.events:
            yield ev


class _CaptureWS:
    """伪 WebSocket——只记录 send_json。"""
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, msg: dict):
        self.sent.append(msg)


@pytest.fixture
def fake_core(monkeypatch):
    core = _FakeCore()
    monkeypatch.setattr(srv, "_core", core)
    return core


@pytest.fixture(autouse=True)
def _reset_state():
    srv._chat_messages.clear()
    srv._chat_agent_ctx = None
    srv._ws_clients.clear()
    srv._ws_names.clear()
    yield
    srv._chat_messages.clear()
    srv._chat_agent_ctx = None
    srv._ws_clients.clear()
    srv._ws_names.clear()


class TestRunAgentStream:
    def test_delta_then_full_message(self, fake_core):
        fake_core.events = [
            ContentDelta(text="你"),
            ContentDelta(text="好"),
            StreamDone(),
        ]
        ws = _CaptureWS()
        srv._ws_clients.append(ws)

        asyncio.run(srv._run_agent_stream("在吗", "张三"))

        types = [m["type"] for m in ws.sent]
        # 契约: typing 开头，delta 逐 token，chat 收尾
        assert types == ["typing", "chat_delta", "chat_delta", "chat"]
        assert ws.sent[1]["content"] == "你"
        final = ws.sent[-1]
        assert final["content"] == "你好"
        assert final["id"] > 0
        assert "time" in final

    def test_reasoning_and_tool_events(self, fake_core):
        fake_core.events = [
            ReasoningDelta(text="思考中"),
            ToolCallStart(tool_name="web_search"),
            ToolCallResult(tool_name="web_search", success=True, output="ok", elapsed_ms=5),
            ContentDelta(text="搜完了"),
            StreamDone(),
        ]
        ws = _CaptureWS()
        srv._ws_clients.append(ws)

        asyncio.run(srv._run_agent_stream("搜一下", "李四"))

        types = [m["type"] for m in ws.sent]
        assert "reasoning" in types
        assert "tool_start" in types
        tool_msg = next(m for m in ws.sent if m["type"] == "tool")
        assert tool_msg["name"] == "web_search" and tool_msg["ok"] is True
        final = next(m for m in ws.sent if m["type"] == "chat")
        assert final["content"] == "搜完了"
        assert final["tools"] == [{"name": "web_search", "ok": True}]

    def test_session_context_reused_across_calls(self, fake_core):
        fake_core.events = [ContentDelta(text="a"), StreamDone()]
        asyncio.run(srv._run_agent_stream("1", "u"))
        ctx1 = srv._chat_agent_ctx
        asyncio.run(srv._run_agent_stream("2", "u"))
        assert srv._chat_agent_ctx is ctx1  # 群聊上下文跨消息保持
        assert fake_core.captured_ctx is ctx1

    def test_auto_mode_restored_after_stream(self, fake_core):
        fake_core._mode = "plan"
        fake_core.events = [StreamDone()]
        asyncio.run(srv._run_agent_stream("x", "u"))
        assert fake_core._mode == "plan"
        assert fake_core._automode is False

    def test_empty_reply_fallback(self, fake_core):
        fake_core.events = [StreamDone()]
        ws = _CaptureWS()
        srv._ws_clients.append(ws)
        asyncio.run(srv._run_agent_stream("x", "u"))
        final = next(m for m in ws.sent if m["type"] == "chat")
        assert final["content"] == "(空回复)"

    def test_message_persisted_to_history(self, fake_core):
        fake_core.events = [ContentDelta(text="入库"), StreamDone()]
        asyncio.run(srv._run_agent_stream("x", "u"))
        assert srv._chat_messages[-1]["from"] == "天枢"
        assert srv._chat_messages[-1]["content"] == "入库"


class TestWSHandlerProtocol:
    """WS 端到端：TestClient 真实握手 + mock core。"""

    def test_chat_message_broadcast_with_id(self, fake_core, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setattr(srv, "LOGIN_PASSWORD", "test-pw")
        with TestClient(srv.app) as client:
            # 绕过登录: 注入 token
            srv._login_tokens.add("ws-test-token")
            try:
                with client.websocket_connect("/ws?token=ws-test-token") as ws:
                    ws.send_json({"type": "chat", "from": "测试者", "content": "hi"})
                    # 略过 status/join 系统消息，取到自己的 chat 为止
                    for _ in range(5):
                        msg = ws.receive_json()
                        if msg.get("type") == "chat":
                            break
                    # 自己发的消息会广播回来，带 id + time（前端增量渲染依赖）
                    assert msg["type"] == "chat"
                    assert msg["from"] == "测试者"
                    assert "id" in msg and "time" in msg
            finally:
                srv._login_tokens.discard("ws-test-token")
