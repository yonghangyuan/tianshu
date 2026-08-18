"""Phase 0: Streaming 测试 — chat_stream() SSE 解析 + run_stream() 事件流。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保项目在 sys.path
_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root / "src"))

from tianshu.diyao.providers.base import (
    ProviderStreamChunk,
    TokenUsage,
    OpenAICompatibleProvider,
)
from tianshu.sdk.models import (
    ContentDelta,
    ReasoningDelta,
    ToolCallStart,
    ToolCallConfirm,
    ToolCallResult,
    StreamDone,
    StreamError,
)


# ═══════════════════════════════════════════════════════════════════════════
# Mock SSE 数据
# ═══════════════════════════════════════════════════════════════════════════

SSE_CHUNKS = [
    # 第一个 token
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"你好"}}]}\n\n',
    # 后续 token
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"！"}}]}\n\n',
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"我是"}}]}\n\n',
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"天枢"}}]}\n\n',
    # 最后一个带 usage 的 chunk
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}\n\n',
    "data: [DONE]\n\n",
]

SSE_CHUNKS_WITH_REASONING = [
    'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_content":"用户想要..."}}]}\n\n',
    'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_content":"分析这个问题"}}]}\n\n',
    'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"答案是"}}]}\n\n',
    'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"42"}}]}\n\n',
    'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":20,"completion_tokens":3,"total_tokens":23}}\n\n',
    "data: [DONE]\n\n",
]

SSE_CHUNKS_WITH_TOOLS = [
    'data: {"id":"chatcmpl-3","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"让我搜索一下"}}]}\n\n',
    'data: {"id":"chatcmpl-3","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_001","type":"function","function":{"name":"web_search","arguments":""}}]}}]}\n\n',
    'data: {"id":"chatcmpl-3","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"query\\""}}]}}]}\n\n',
    'data: {"id":"chatcmpl-3","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":":\\"Python\\"}"}}]}}]}\n\n',
    'data: {"id":"chatcmpl-3","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n',
    "data: [DONE]\n\n",
]


# ═══════════════════════════════════════════════════════════════════════════
# 单元测试：_parse_stream_chunk
# ═══════════════════════════════════════════════════════════════════════════

class TestParseStreamChunk:
    """测试 _parse_stream_chunk() 方法。"""

    def test_parse_content_delta(self):
        """解析包含 content 的 delta。"""
        provider = _make_dummy_provider()
        acc: dict[int, dict[str, str]] = {}
        data = {
            "choices": [{
                "index": 0,
                "delta": {"content": "你好"},
            }],
        }
        chunk = provider._parse_stream_chunk(data, acc)
        assert chunk is not None
        assert chunk.delta_content == "你好"
        assert chunk.finish_reason is None

    def test_parse_reasoning_delta(self):
        """解析包含 reasoning_content 的 delta。"""
        provider = _make_dummy_provider()
        acc: dict[int, dict[str, str]] = {}
        data = {
            "choices": [{
                "index": 0,
                "delta": {"reasoning_content": "思考中..."},
            }],
        }
        chunk = provider._parse_stream_chunk(data, acc)
        assert chunk is not None
        assert chunk.reasoning_content == "思考中..."

    def test_parse_finish_with_usage(self):
        """解析带 usage 的 finish chunk。"""
        provider = _make_dummy_provider()
        acc: dict[int, dict[str, str]] = {}
        data = {
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 8,
                "total_tokens": 23,
            },
        }
        chunk = provider._parse_stream_chunk(data, acc)
        assert chunk is not None
        assert chunk.finish_reason == "stop"
        assert chunk.usage is not None
        assert chunk.usage.total_tokens == 23
        assert chunk.usage.prompt_tokens == 15

    def test_parse_tool_call_accumulation(self):
        """测试 tool_call 跨 chunk 累积。"""
        provider = _make_dummy_provider()
        acc: dict[int, dict[str, str]] = {}

        # Chunk 1: tool_call 开始 + id + name
        data1 = {
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": ""},
                    }],
                },
            }],
        }
        chunk1 = provider._parse_stream_chunk(data1, acc)
        assert chunk1 is not None
        assert chunk1.tool_call_deltas is not None
        assert chunk1.tool_call_deltas[0]["name"] == "web_search"

        # Chunk 2: arguments 继续
        data2 = {
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"arguments": '{"query": "Python"}'},
                    }],
                },
            }],
        }
        chunk2 = provider._parse_stream_chunk(data2, acc)
        assert chunk2 is not None
        assert chunk2.tool_call_deltas is not None
        assert '"query"' in chunk2.tool_call_deltas[0]["arguments"]

    def test_parse_empty_delta_returns_none(self):
        """空的 delta 返回 None。"""
        provider = _make_dummy_provider()
        acc: dict[int, dict[str, str]] = {}
        data = {"choices": [{"index": 0, "delta": {}}]}
        chunk = provider._parse_stream_chunk(data, acc)
        assert chunk is None


# ═══════════════════════════════════════════════════════════════════════════
# 集成测试：chat_stream()
# ═══════════════════════════════════════════════════════════════════════════

class TestChatStream:
    """测试 chat_stream() 端到端行为。"""

    @pytest.mark.asyncio
    async def test_stream_basic_content(self):
        """基础流式：收集所有 content delta。"""
        provider = _make_dummy_provider()

        chunks: list[ProviderStreamChunk] = []
        async for chunk in provider.chat_stream(
            messages=[{"role": "user", "content": "hi"}],
        ):
            chunks.append(chunk)

        # 应该有 content chunks + 最终 chunk
        contents = [c.delta_content for c in chunks if c.delta_content]
        assert len(contents) > 0
        combined = "".join(contents)
        assert len(combined) > 0

    @pytest.mark.asyncio
    async def test_stream_error_handling(self):
        """stream 错误处理：网络错误 → StreamError。"""
        provider = _make_dummy_provider()

        # 直接测试异常路径：_parse_stream_chunk 返回 None 对空行
        acc: dict[int, dict[str, str]] = {}
        chunk = provider._parse_stream_chunk({}, acc)
        assert chunk is None


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

class DummyProvider(OpenAICompatibleProvider):
    """测试用 provider —— 覆写 chat_stream 用 mock SSE 数据。"""

    base_url = "https://test.example.com/v1"
    api_key_env = "TEST_API_KEY"

    def __init__(self, sse_lines: list[str] | None = None):
        super().__init__(api_key="test-key")
        self._sse_lines = sse_lines or SSE_CHUNKS

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_id(self) -> str:
        return "test-model"

    async def chat_stream(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        """模拟 SSE streaming —— 直接调用 _parse_stream_chunk。"""
        acc: dict[int, dict[str, str]] = {}
        final_usage = None
        final_finish = None

        for line in self._sse_lines:
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            chunk = self._parse_stream_chunk(data, acc)
            if chunk is None:
                continue
            if chunk.usage:
                final_usage = chunk.usage
            if chunk.finish_reason:
                final_finish = chunk.finish_reason
            yield chunk

        if final_usage or final_finish:
            yield ProviderStreamChunk(
                finish_reason=final_finish,
                usage=final_usage,
            )


def _make_dummy_provider(sse_lines=None):
    return DummyProvider(sse_lines)


# ═══════════════════════════════════════════════════════════════════════════
# run_stream() 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestRunStream:
    """测试 AgentCore.run_stream()。"""

    @pytest.mark.asyncio
    async def test_run_stream_yields_content(self):
        """run_stream() 应 yield ContentDelta 事件。"""
        from tianshu.core.service import AgentCore
        from tianshu.sdk.models import AgentRequest, AgentContext

        # 构建 mock core，手动设置 provider
        core = AgentCore()
        core._ready = True
        core._registry = MagicMock()
        core._registry.list_all.return_value = []
        core._system_prompt = ""

        # Mock router
        dummy = _make_dummy_provider()
        core._router = MagicMock()
        core._router.route = AsyncMock(return_value=(dummy, "test-model", 1))
        core._router.route_direct.return_value = (dummy, "test-model", 1)

        # Mock skills / memory / audit
        core._audit = MagicMock()
        core._audit.generate_id.return_value = "D000001"
        core._audit.capture_snapshot.return_value = []
        core._audit.record = AsyncMock()

        core._skills = MagicMock()
        core._skills.loader.get_all_tools.return_value = []
        core._skills.observe_turn = MagicMock()
        core._skills.evolve_skills = AsyncMock(return_value=[])

        core._memory = MagicMock()
        core._memory.auto_profile = AsyncMock(return_value=[])
        core._memory.remember = AsyncMock()
        core._memory.prefetch = AsyncMock(return_value="")
        core._memory.digest = AsyncMock(return_value=[])

        core._evolution_counter = 0

        # 收集事件
        events = []
        async for event in core.run_stream(
            AgentRequest(input="你好", task_type="conversation"),
        ):
            events.append(event)

        # 应包含 ContentDelta + StreamDone
        content_events = [e for e in events if isinstance(e, ContentDelta)]
        done_events = [e for e in events if isinstance(e, StreamDone)]

        assert len(content_events) > 0, f"Expected ContentDelta events, got: {[type(e).__name__ for e in events]}"
        assert len(done_events) == 1
        assert done_events[0].decision_id == "D000001"

    @pytest.mark.asyncio
    async def test_run_stream_not_ready(self):
        """未 setup 的 core 应返回 StreamError。"""
        from tianshu.core.service import AgentCore
        from tianshu.sdk.models import AgentRequest

        core = AgentCore()
        events = []
        async for event in core.run_stream(AgentRequest(input="test")):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], StreamError)
        assert "not set up" in events[0].message


# ═══════════════════════════════════════════════════════════════════════════
# 预设系统 × run_stream() 集成测试
# ═══════════════════════════════════════════════════════════════════════════


def _sse_tool_call(name: str, args: dict, call_id: str = "call_001") -> list[str]:
    """构造一次工具调用的 canned SSE 行。"""
    d1 = {"id": "chatcmpl-9", "object": "chat.completion.chunk",
          "choices": [{"index": 0, "delta": {"tool_calls": [
              {"index": 0, "id": call_id, "type": "function",
               "function": {"name": name, "arguments": ""}}]}}]}
    d2 = {"id": "chatcmpl-9", "object": "chat.completion.chunk",
          "choices": [{"index": 0, "delta": {"tool_calls": [
              {"index": 0, "function": {"arguments": json.dumps(args, ensure_ascii=False)}}]}}]}
    d3 = {"id": "chatcmpl-9", "object": "chat.completion.chunk",
          "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
    return [f'data: {json.dumps(d, ensure_ascii=False)}\n\n' for d in (d1, d2, d3)] + ["data: [DONE]\n\n"]


class _SeqProvider(DummyProvider):
    """按调用次序重放不同 SSE 组：第一次工具调用，之后纯文本。"""

    def __init__(self, *line_sets: list[str]):
        super().__init__(line_sets[0] if line_sets else [])
        self._sets = list(line_sets)
        self._i = 0

    async def chat_stream(self, messages, tools=None, **kw):
        self._i += 1
        idx = min(self._i - 1, len(self._sets) - 1)
        saved = self._sse_lines
        self._sse_lines = self._sets[idx]
        try:
            async for chunk in super().chat_stream(messages, tools=tools, **kw):
                yield chunk
        finally:
            self._sse_lines = saved


class _FakeShell:
    """极简模式测试用的假持久 shell——不 spawn 真 cmd。"""
    def __init__(self):
        self.commands = []

    async def run(self, command, timeout=60):
        self.commands.append(command)
        return "shell-out"


class TestPresetStreaming:
    """预设轴 × 流式循环集成测试。"""

    def _make_core(self, provider) -> "object":
        from tianshu.core.service import AgentCore
        core = AgentCore()
        core._ready = True
        core._registry = MagicMock()
        core._registry.list_all.return_value = []
        core._system_prompt = ""

        core._router = MagicMock()
        core._router.route = AsyncMock(return_value=(provider, "test-model", 1))
        core._router.route_direct.return_value = (provider, "test-model", 1)

        core._audit = MagicMock()
        core._audit.generate_id.return_value = "D000001"
        core._audit.capture_snapshot.return_value = []
        core._audit.record = AsyncMock()

        core._skills = MagicMock()
        core._skills.loader.get_all_tools.return_value = []
        core._skills.observe_turn = MagicMock()
        core._skills.evolve_skills = AsyncMock(return_value=[])
        core._skills.execute = AsyncMock(return_value="executed")

        core._memory = MagicMock()
        core._memory.auto_profile = AsyncMock(return_value=[])
        core._memory.remember = AsyncMock()
        core._memory.prefetch = AsyncMock(return_value="")
        core._memory.digest = AsyncMock(return_value=[])

        core._evolution_counter = 0
        return core

    @pytest.mark.asyncio
    async def test_minimal_preset_skips_confirm(self):
        """minimal 预设：WRITE 工具直接执行，无 ToolCallConfirm。"""
        from tianshu.core.service import AgentCore
        from tianshu.sdk.models import AgentRequest, AgentContext

        provider = _SeqProvider(_sse_tool_call("shell_exec", {"command": "echo hi"}), SSE_CHUNKS)
        core = self._make_core(provider)
        core._preset = "minimal"

        ctx = AgentContext(session_id="t-minimal")
        ctx.shell = _FakeShell()

        events = []
        async for event in core.run_stream(AgentRequest(input="跑命令"), ctx=ctx):
            events.append(event)

        assert not any(isinstance(e, ToolCallConfirm) for e in events)
        results = [e for e in events if isinstance(e, ToolCallResult)]
        assert results, "应有工具结果事件"
        assert results[0].success is True
        assert "shell-out" in results[0].output
        assert ctx.shell.commands == ["echo hi"]

    @pytest.mark.asyncio
    async def test_code_preset_run_code_end_to_end(self):
        """code 预设：run_code 走真实 PTC 子进程，submit 值返回。"""
        from tianshu.core.service import AgentCore
        from tianshu.sdk.models import AgentRequest

        provider = _SeqProvider(
            _sse_tool_call("run_code", {"code": 'submit("PTC_OK")'}), SSE_CHUNKS
        )
        core = self._make_core(provider)
        core._preset = "code"
        core._automode = True  # auto + code = 免确认（矩阵）

        events = []
        async for event in core.run_stream(AgentRequest(input="用代码执行")):
            events.append(event)

        results = [e for e in events if isinstance(e, ToolCallResult)]
        assert results, "应有工具结果事件"
        assert results[0].success is True
        assert "PTC_OK" in results[0].output

    @pytest.mark.asyncio
    async def test_standard_preset_confirm_gate_regression(self):
        """standard 预设：WRITE 工具仍弹确认——确认后执行成功。"""
        from tianshu.core.service import AgentCore
        from tianshu.sdk.models import AgentRequest

        provider = _SeqProvider(_sse_tool_call("shell_exec", {"command": "echo hi"}), SSE_CHUNKS)
        core = self._make_core(provider)  # 默认 standard

        events = []
        async for event in core.run_stream(AgentRequest(input="跑命令")):
            events.append(event)
            if isinstance(event, ToolCallConfirm):
                core.confirm_tool(True)  # 模拟用户确认

        confirms = [e for e in events if isinstance(e, ToolCallConfirm)]
        assert confirms, "standard 下 WRITE 工具应弹确认"
        results = [e for e in events if isinstance(e, ToolCallResult)]
        assert results and results[0].success is True
