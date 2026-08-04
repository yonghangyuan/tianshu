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
    """跨时间尺度仲裁——旧版兼容 + 新版贝叶斯融合。"""

    def test_legacy_arbitrate_still_works(self):
        """旧版 arbitrate() 仍可调用。"""
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
            payload={"temperature": 85.0},
        )
        stale_msg = TrigramMessage(
            decision_id="d2", timestamp=now_ms - 600_000, ttl_ms=600_000,
            source=slow.ref, target=AgentRef(Layer.REN, "planner"), intent="旧数据",
            payload={"temperature": 75.0},
        )

        result = arbitrate([(fresh_msg, fast), (stale_msg, slow)], entity_id="test")
        assert not result.conflict
        assert result.winner == fast.ref

    def test_bayesian_fuse_precision_weighted(self):
        """贝叶斯融合：精密传感器权重远大于标准传感器。"""
        import time
        from tianshu.sdk.trigram import (
            bayesian_fuse, EntityDynamics, SensorCharacteristics,
        )

        now = time.time()
        entity = EntityDynamics.from_preset("fast")
        precision = SensorCharacteristics.from_preset("precision")
        standard = SensorCharacteristics.from_preset("standard")

        result = bayesian_fuse([
            (85.0, now - 5, precision),
            (75.0, now - 3600, standard),
        ], entity)

        # 精密传感器应占绝对主导
        assert result.source_count == 2
        assert result.contributions[0]["weight_pct"] > 90  # 精密 > 90%
        assert result.posterior_variance < 0.1  # 高精度

    def test_bayesian_fuse_single_source(self):
        """单源融合 → 后验 = 单源。"""
        import time
        from tianshu.sdk.trigram import (
            bayesian_fuse, EntityDynamics, SensorCharacteristics,
        )

        now = time.time()
        entity = EntityDynamics.from_preset("static")
        sensor = SensorCharacteristics.from_preset("precision")

        result = bayesian_fuse([(42.0, now, sensor)], entity)
        assert result.source_count == 1
        assert abs(result.posterior_mean - 42.0) < 0.001

    def test_feedback_learning_penalizes_outliers(self):
        """反馈学习：严重偏离 ground truth → 可靠性下降。"""
        import time
        from tianshu.sdk.trigram import (
            update_sensor_reliability, EntityDynamics, SensorCharacteristics,
        )

        now = time.time()
        entity = EntityDynamics.from_preset("fast")

        # 偏离小的传感器 → 可靠性保持高位
        good = SensorCharacteristics.from_preset("precision")
        update_sensor_reliability(good, 85.0, now - 5, 84.8, entity)
        assert good.reliability_score > 0.95

        # 偏离大的传感器 → 可靠性显著下降
        bad = SensorCharacteristics.from_preset("precision")
        old_score = bad.reliability_score
        update_sensor_reliability(bad, 85.0, now - 5, 70.0, entity)
        assert bad.reliability_score < old_score

    def test_entity_presets_exist(self):
        """所有预设实体类型可创建。"""
        from tianshu.sdk.trigram import EntityDynamics

        for etype in ["static", "slow", "fast", "ultra_fast"]:
            e = EntityDynamics.from_preset(etype)
            assert e.entity_type == etype
            assert e.process_noise_per_second >= 0

    def test_sensor_presets_exist(self):
        """所有预设传感器精度可创建。"""
        from tianshu.sdk.trigram import SensorCharacteristics

        for pname in ["precision", "standard", "coarse", "human"]:
            s = SensorCharacteristics.from_preset(pname)
            assert s.observation_variance > 0
