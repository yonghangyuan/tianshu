"""Core unit tests — no API keys needed."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tianshu.sdk.models import (
    AgentRequest, AgentResponse, AgentContext,
    ToolCall, TokenUsage, ProviderResponse,
    AuditRecord, AuditLevel, SkillDef,
)
from tianshu.sdk.provider import BaseProvider
from tianshu.diyao.providers.registry import ProviderRegistry
from tianshu.renyao.skills.loader import SkillLoader
from tianshu.renyao.skills.observer import SkillObserver
from tianshu.memory.service import MemoryService


class TestSDKModels:
    def test_agent_request(self):
        req = AgentRequest(input="hello")
        assert req.input == "hello"
        assert req.task_type == "conversation"

    def test_agent_response(self):
        resp = AgentResponse(decision_id="D001", content="hi", model_used="deepseek/v4-pro")
        assert resp.decision_id == "D001"

    def test_audit_record(self):
        r = AuditRecord(decision_id="D001", llm_model="test", level=3)
        assert r.level == 3

    def test_skill_def(self):
        sd = SkillDef(name="test-skill", description="test", trigram="地+人")
        assert sd.trigram == "地+人"


class TestProviderRegistry:
    def test_register_and_get(self):
        reg = ProviderRegistry()

        class MockProvider(BaseProvider):
            provider_name = "mock"
            model_id = "mock-1"
            async def chat(self, messages, **kw):
                return ProviderResponse(content="ok")
            async def is_available(self):
                return True

        reg.register(MockProvider())
        p = reg.get("mock", "mock-1")
        assert p is not None
        assert p.provider_name == "mock"

    def test_get_missing(self):
        reg = ProviderRegistry()
        assert reg.get("nonexistent") is None


class TestSkillObserver:
    def test_empty_observer(self):
        obs = SkillObserver()
        patterns = obs.check_evolution()
        assert patterns == []


class TestMemoryService:
    @pytest.mark.asyncio
    async def test_remember_and_recall(self):
        mem = MemoryService(base_dir=Path("/tmp/tianshu_test_memory"))
        await mem.remember("test_key", "test_value", "test")
        results = await mem.recall("test")
        assert len(results) > 0


class TestRouter:
    def test_route_direct(self):
        from tianshu.core.router import ModelRouter, RoutingConfig

        reg = ProviderRegistry()
        class MockP(BaseProvider):
            provider_name = "deepseek"
            model_id = "deepseek-v4-pro"
            async def chat(self, messages, **kw):
                return ProviderResponse(content="ok")
            async def is_available(self):
                return True

        reg.register(MockP())
        router = ModelRouter(RoutingConfig(), reg)
        p, mid, level = router.route_direct("deepseek", "v4-pro")
        assert p is not None
        assert mid == "deepseek-v4-pro"
        assert level == AuditLevel.FULL


class TestTrigramChannel:
    """三爻消息通道 demo —— 天·人·地三层验证。"""

    @pytest.mark.asyncio
    async def test_normal_tool_passes(self):
        """正常工具（web_search）通过三层闸门。"""
        from tianshu.core.service import AgentCore
        from tianshu.diyao.providers.registry import ProviderRegistry
        from tianshu.core.router import RoutingConfig

        core = AgentCore()
        reg = ProviderRegistry()
        class MockP(BaseProvider):
            provider_name = "deepseek"
            model_id = "deepseek-chat"
            async def chat(self, messages, **kw):
                return ProviderResponse(content="ok")
            async def is_available(self):
                return True

        reg.register(MockP())
        core.setup(registry=reg, routing=RoutingConfig(fallback="deepseek/chat"),
                   system_prompt="")

        result = await core.execute_via_trigram(
            "web_search", {"query": "test"}
        )

        assert result["allowed"] is True
        assert len(result["message_chain"]) == 3  # di→ren + ren→tian + result
        assert result["audit"] is not None
        assert result["audit"]["who"]["layer"] == "di"

    @pytest.mark.asyncio
    async def test_dangerous_tool_blocked(self):
        """危险命令（rm -rf）被天层否决。"""
        from tianshu.core.service import AgentCore
        from tianshu.diyao.providers.registry import ProviderRegistry
        from tianshu.core.router import RoutingConfig

        core = AgentCore()
        reg = ProviderRegistry()
        class MockP(BaseProvider):
            provider_name = "deepseek"
            model_id = "deepseek-chat"
            async def chat(self, messages, **kw):
                return ProviderResponse(content="ok")
            async def is_available(self):
                return True

        reg.register(MockP())
        core.setup(registry=reg, routing=RoutingConfig(fallback="deepseek/chat"),
                   system_prompt="")

        result = await core.execute_via_trigram(
            "shell_exec", {"command": "rm -rf / --no-preserve-root"}
        )

        assert result["allowed"] is False
        assert "否决" in result["result"]
        # 消息链最后一条是天层的 OVERRIDE
        override_msg = result["message_chain"][-1]
        assert override_msg["priority"] == "OVERRIDE"
        assert override_msg["source"]["layer"] == "tian"


class TestTimeArbitration:
    """跨时间尺度仲裁——天层根据衰减函数裁定信任哪个信息源。"""

    def test_fast_beats_stale(self):
        """新数据置信度高 → 胜出。"""
        import time
        from tianshu.sdk.trigram import (
            arbitrate, TrigramMessage, AgentRef, Layer, AgentRegistration,
            TimeScale, InfoDecayConfig, SyncMode, LayerPermission,
        )

        now_ms = int(time.time() * 1000)
        fast = AgentRegistration(
            ref=AgentRef(Layer.DI, "fast_sensor"),
            time_scale=TimeScale(tick_ms=100, decay=InfoDecayConfig(half_life_ms=60_000)),
            permissions=[LayerPermission.EXECUTE],
        )
        slow = AgentRegistration(
            ref=AgentRef(Layer.DI, "slow_sensor"),
            time_scale=TimeScale(tick_ms=3_600_000, decay=InfoDecayConfig(half_life_ms=600_000)),
            permissions=[LayerPermission.EXECUTE],
        )

        fresh_msg = TrigramMessage(
            decision_id="d1", timestamp=now_ms - 1_000, ttl_ms=60_000,
            source=fast.ref, target=AgentRef(Layer.REN, "planner"), intent="新数据",
        )
        stale_msg = TrigramMessage(
            decision_id="d2", timestamp=now_ms - 600_000, ttl_ms=600_000,
            source=slow.ref, target=AgentRef(Layer.REN, "planner"), intent="旧数据",
        )

        result = arbitrate([(fresh_msg, fast), (stale_msg, slow)], entity_id="test")

        assert not result.conflict
        # 1秒前 vs 10分钟前 → 快传感器胜出
        assert result.winner == fast.ref

    def test_near_tie_flags_conflict(self):
        """置信度相近 → 标记为冲突。"""
        import time
        from tianshu.sdk.trigram import (
            arbitrate, TrigramMessage, AgentRef, Layer, AgentRegistration,
            TimeScale, InfoDecayConfig, SyncMode, LayerPermission,
        )

        now_ms = int(time.time() * 1000)
        a1 = AgentRegistration(
            ref=AgentRef(Layer.DI, "sensor_a"),
            time_scale=TimeScale(tick_ms=1000, decay=InfoDecayConfig(half_life_ms=60_000)),
            permissions=[LayerPermission.EXECUTE],
        )
        a2 = AgentRegistration(
            ref=AgentRef(Layer.DI, "sensor_b"),
            time_scale=TimeScale(tick_ms=1000, decay=InfoDecayConfig(half_life_ms=60_000)),
            permissions=[LayerPermission.EXECUTE],
        )

        # 两个消息几乎同时到达，相同半衰 → 置信度几乎相同
        m1 = TrigramMessage(
            decision_id="d_a", timestamp=now_ms - 2_000, ttl_ms=60_000,
            source=a1.ref, target=AgentRef(Layer.REN, "planner"), intent="报告A",
        )
        m2 = TrigramMessage(
            decision_id="d_b", timestamp=now_ms - 3_000, ttl_ms=60_000,
            source=a2.ref, target=AgentRef(Layer.REN, "planner"), intent="报告B",
        )

        result = arbitrate([(m1, a1), (m2, a2)], entity_id="test")
        assert result.conflict  # 差值仅 1 秒，应在阈值内
