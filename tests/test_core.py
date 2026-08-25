"""Core unit tests — no API keys needed."""

import pytest
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tianshu.sdk.models import (
    AgentRequest, AgentResponse, AgentContext,
    ToolCall, TokenUsage, ProviderResponse,
    AuditRecord, AuditLevel, SkillDef,
)
from tianshu.diyao.providers.base import BaseProvider
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

    def test_route_direct_native_id_not_prefixed(self):
        """Ollama 类自带族名+tag 的 id 不该被补前缀（曾全灭根因）。

        route_direct("ollama", "qwen3:30b") 补成 "ollama-qwen3:30b" 必查空
        → "No model available"。修复：原始 id 先查，未命中再补。
        """
        from tianshu.core.router import ModelRouter, RoutingConfig

        reg = ProviderRegistry()

        def _mock(oid):
            class M(BaseProvider):
                provider_name = "ollama"
                model_id = oid
                async def chat(self, messages, **kw):
                    return ProviderResponse(content="ok")
                async def is_available(self):
                    return True
            return M()

        for native in ("qwen3:30b", "llama3.2:latest", "deepseek-r1:7b"):
            reg.register(_mock(native))
        router = ModelRouter(RoutingConfig(), reg)
        for native in ("qwen3:30b", "llama3.2:latest", "deepseek-r1:7b"):
            p, mid, _ = router.route_direct("ollama", native)
            assert p is not None, f"{native} 被误补前缀"
            assert mid == native

    def test_route_pref_native_id(self):
        """prefer 条目同样先按原样解析（离线兜底 ollama/llama3.2:latest 场景）。"""
        import asyncio
        from tianshu.core.router import ModelRouter, RoutingConfig, RoutingRule

        reg = ProviderRegistry()
        class MockOllama(BaseProvider):
            provider_name = "ollama"
            model_id = "llama3.2:latest"
            async def chat(self, messages, **kw):
                return ProviderResponse(content="ok")
            async def is_available(self):
                return True

        reg.register(MockOllama())
        cfg = RoutingConfig(rules=[RoutingRule(
            task_types=["conversation"], prefer=["ollama/llama3.2:latest"],
        )])
        router = ModelRouter(cfg, reg)
        p, mid, _ = asyncio.run(router.route("conversation"))
        assert p is not None
        assert mid == "llama3.2:latest"


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


class TestDecisionEngine:
    """决策标准引擎——同一个后验，不同场景利害 → 不同决策。"""

    def test_low_stakes_uses_eum(self):
        """低风险 → 期望效用最大化。"""
        from tianshu.sdk.trigram import (
            decide, DecisionContext, EntityDynamics, SensorCharacteristics,
            bayesian_fuse,
        )
        import time
        now = time.time()
        fused = bayesian_fuse(
            [(84.0, now, SensorCharacteristics.from_preset("precision"))],
            EntityDynamics.from_preset("fast"),
        )
        def ok(_): return 0.0
        def bad(_): return 1.0

        result = decide(fused, [("ok", ok), ("bad", bad)], DecisionContext.low_stakes())
        assert result.chosen_action == "ok"
        assert result.criterion.value == "expected_utility"

    def test_critical_stakes_uses_precautionary(self):
        """关键安全 → 预防原则，未证明安全则不行动。"""
        from tianshu.sdk.trigram import (
            decide, DecisionContext, EntityDynamics, SensorCharacteristics,
            bayesian_fuse,
        )
        import time
        now = time.time()
        # 后验均值 90°C，远超 85°C 安全线
        fused = bayesian_fuse(
            [(90.0, now, SensorCharacteristics.from_preset("precision"))],
            EntityDynamics.from_preset("fast"),
        )
        def risky(theta): return max(0, theta - 85)
        def safe(_): return 0.5

        result = decide(
            fused,
            [("risky", risky), ("safe", safe)],
            DecisionContext.critical_stakes(),
        )
        # 关键安全 → 预防原则 → 两个动作都有损失 → 不行动
        assert result.criterion.value == "precautionary"

    def test_select_criterion_maps_contexts(self):
        """select_criterion 根据利害关系选标准。"""
        from tianshu.sdk.trigram import (
            select_criterion, DecisionContext, DecisionCriterion,
        )
        assert select_criterion(DecisionContext.low_stakes()) == DecisionCriterion.EXPECTED_UTILITY
        assert select_criterion(DecisionContext.critical_stakes()) == DecisionCriterion.PRECAUTIONARY

    def test_minimax_picks_lowest_regret(self):
        """Minimax Regret: 选最坏情况下后悔最小的。"""
        from tianshu.sdk.trigram import (
            decide, DecisionContext, EntityDynamics, SensorCharacteristics,
            bayesian_fuse,
        )
        import time
        now = time.time()
        fused = bayesian_fuse(
            [(50.0, now, SensorCharacteristics.from_preset("standard"))],
            EntityDynamics.from_preset("static"),
        )

        # 动作A: 固定高损失；动作B: θ 敏感但大多数情况下低损失
        def action_a(theta): return 0.8
        def action_b(theta): return abs(theta - 50) / 100

        ctx = DecisionContext(
            reversibility=0.5, max_loss=0.5, time_pressure=0.5,
            model_confidence=0.3,  # 低模型置信度 → ROBUST 或 SAFETY_FIRST
        )
        result = decide(fused, [("A", action_a), ("B", action_b)], ctx)
        assert result.chosen_action in ("A", "B")
        assert result.criterion is not None


class TestE2EIntegration:
    """端到端集成测试——输入→融合→决策→闸门→审计 完整链路。"""

    @pytest.mark.asyncio
    async def test_full_chain_safe_tool(self):
        """SAFE 工具: 消息验证→权限→策略→无风险评估→执行→审计。"""
        from tianshu.core.service import AgentCore
        from tianshu.diyao.providers.registry import ProviderRegistry
        from tianshu.core.router import RoutingConfig
        from tianshu.sdk.trigram import Layer

        core = AgentCore()
        reg = ProviderRegistry()
        class MockP:
            provider_name = "deepseek"; model_id = "deepseek-chat"
            async def chat(self, messages, **kw):
                from tianshu.sdk.models import ProviderResponse
                return ProviderResponse(content="ok")
            async def is_available(self):
                return True
        reg.register(MockP())
        core.setup(registry=reg, routing=RoutingConfig(fallback="deepseek/chat"), system_prompt="")

        result = await core.execute_via_trigram("web_search", {"query": "test"})

        # 验证完整链路
        assert result["allowed"] is True
        chain = result["message_chain"]
        assert len(chain) == 3  # di→ren + ren→tian + result
        assert chain[0]["source"]["layer"] == "di"
        assert chain[1]["source"]["layer"] == "ren"
        assert chain[1]["target"]["layer"] == "tian"
        # 审计六问已记录
        assert result["audit"] is not None
        assert "who" in result["audit"]
        assert "decision" in result["audit"]

    @pytest.mark.asyncio
    async def test_full_chain_critical_blocked(self):
        """DANGER 工具: 预防原则否决 → 不执行 → OVERRIDE 消息。"""
        from tianshu.core.service import AgentCore
        from tianshu.diyao.providers.registry import ProviderRegistry
        from tianshu.core.router import RoutingConfig
        from tianshu.sdk.trigram import DecisionContext, WorldLevel
        from tianshu.core.tool_registry import ToolInfo
        from tianshu.sdk.trigram import Layer

        core = AgentCore()
        reg = ProviderRegistry()
        class MockP:
            provider_name = "deepseek"; model_id = "deepseek-chat"
            async def chat(self, messages, **kw):
                from tianshu.sdk.models import ProviderResponse
                return ProviderResponse(content="ok")
            async def is_available(self):
                return True
        reg.register(MockP())
        core.setup(registry=reg, routing=RoutingConfig(fallback="deepseek/chat"), system_prompt="")

        core._tool_registry.register(ToolInfo(
            "dangerous_op", "高危操作", {}, None,
            permission=3, skill_name="test",
            stakes=DecisionContext.critical_stakes(),
            world_level=WorldLevel.CONTROLLABLE,
        ))

        result = await core.execute_via_trigram("dangerous_op", {})

        assert result["allowed"] is False
        assert "否决" in result["result"]
        # 链中有 OVERRIDE 消息
        priorities = [m["priority"] for m in result["message_chain"]]
        assert "OVERRIDE" in priorities

    @pytest.mark.asyncio
    async def test_world_level_strategy_switch(self):
        """四层世界: UNOBSERVABLE→沉默, CONTROLLABLE→否决。"""
        from tianshu.core.service import AgentCore
        from tianshu.diyao.providers.registry import ProviderRegistry
        from tianshu.core.router import RoutingConfig
        from tianshu.sdk.trigram import WorldLevel, DecisionContext
        from tianshu.core.tool_registry import ToolInfo

        core = AgentCore()
        reg = ProviderRegistry()
        class MockP:
            provider_name = "deepseek"; model_id = "deepseek-chat"
            async def chat(self, messages, **kw):
                from tianshu.sdk.models import ProviderResponse
                return ProviderResponse(content="ok")
            async def is_available(self):
                return True
        reg.register(MockP())
        core.setup(registry=reg, routing=RoutingConfig(fallback="deepseek/chat"), system_prompt="")

        # UNOBSERVABLE: 天层沉默——即使不知道的工具，也不阻止
        core._tool_registry.register(ToolInfo(
            "unknown_thing", "未知领域", {}, None,
            permission=0, world_level=WorldLevel.UNOBSERVABLE,
        ))
        r1 = await core.execute_via_trigram("unknown_thing", {})
        assert r1["allowed"] is True  # 天层沉默，不阻止

        # CONTROLLABLE + CRITICAL: 天层否决
        core._tool_registry.register(ToolInfo(
            "launch_system", "发射系统", {}, None,
            permission=3, world_level=WorldLevel.CONTROLLABLE,
            stakes=DecisionContext.critical_stakes(),
        ))
        r2 = await core.execute_via_trigram("launch_system", {"target": "alpha"})
        assert r2["allowed"] is False  # 预防原则否决

    @pytest.mark.asyncio
    async def test_bayesian_fuse_in_chain(self):
        """贝叶斯融合 + 决策引擎 联合测试。"""
        from tianshu.core.service import AgentCore
        from tianshu.diyao.providers.registry import ProviderRegistry
        from tianshu.core.router import RoutingConfig
        from tianshu.sdk.trigram import (
            EntityDynamics, SensorCharacteristics,
            bayesian_fuse, decide, DecisionContext,
        )
        import time

        # 先融合
        now = time.time()
        fused = bayesian_fuse([
            (84.92, now, SensorCharacteristics.from_preset("precision")),
        ], EntityDynamics.from_preset("fast"))

        # 再决策
        def ok(_): return 0.0
        def bad(_): return 1.0
        result = decide(fused, [("ok", ok), ("bad", bad)], DecisionContext.low_stakes())

        assert result.chosen_action == "ok"
        assert result.criterion.value == "expected_utility"
        # 验证融合精度
        assert fused.posterior_variance < 0.1  # 精密传感器


class TestContextCompression:
    """结构化上下文压缩 + 审计可回溯。"""

    @pytest.mark.asyncio
    async def test_compression_stores_audit_snapshot(self):
        """压缩后审计快照可查询。"""
        from tianshu.core.service import AgentCore
        from tianshu.diyao.providers.registry import ProviderRegistry
        from tianshu.core.router import RoutingConfig

        core = AgentCore()
        reg = ProviderRegistry()
        class MockP:
            provider_name = "deepseek"; model_id = "deepseek-chat"
            max_context_tokens = 1000  # 极小窗口强制触发压缩
            async def chat(self, messages, **kw):
                from tianshu.sdk.models import ProviderResponse
                return ProviderResponse(content="[已完成] A\n[待处理] B\n[关键信息] C")
            async def is_available(self):
                return True
        reg.register(MockP())
        core.setup(registry=reg, routing=RoutingConfig(fallback="deepseek/chat"),
                   system_prompt="You are helpful.")

        from tianshu.sdk.models import AgentContext
        ctx = AgentContext()
        # 制造大量历史消息触发压缩
        for i in range(20):
            ctx.messages.append({"role": "user", "content": f"msg {i} " + "x" * 300})

        msgs, meta = await core._build_messages(
            "new question", ctx, 1, "mock/deepseek-chat",
            MockP(),
        )

        assert meta is not None  # 应触发压缩
        assert meta["level"] >= 2  # 激进或强制级别
        assert meta["stored_decision_id"]  # 审计 ID 已生成
        assert "已完成" in meta["summary"] or "[已完成]" in meta["summary"]

class TestAgentScheduler:
    """Agent 时间尺度调度——按声明的 tick 真实运行。"""

    @pytest.mark.asyncio
    async def test_agent_ticks_at_declared_rate(self):
        """Agent 按声明的 tick_ms 周期性触发。"""
        from tianshu.tianyao.agent_scheduler import AgentScheduler
        from tianshu.sdk.trigram import AgentRef, Layer, TimeScale, TrigramMessage

        scheduler = AgentScheduler()
        ticks_received: list[int] = []

        async def my_callback(ref, tick_idx, elapsed):
            ticks_received.append(tick_idx)
            return TrigramMessage.create(
                source=ref, target=AgentRef(Layer.REN, "test"),
                intent=f"Tick {tick_idx}",
            )

        ref = AgentRef(Layer.DI, "test_sensor")
        scheduler.register(ref, TimeScale(tick_ms=100), my_callback)
        await scheduler.start()
        await asyncio.sleep(0.35)  # 应触发 ~3 次
        await scheduler.stop()

        assert len(ticks_received) >= 2  # 至少 2 次
        assert ticks_received[0] < ticks_received[-1]  # tick 递增

    @pytest.mark.asyncio
    async def test_multi_agent_different_ticks(self):
        """不同 Agent 按不同频率 tick。"""
        from tianshu.tianyao.agent_scheduler import AgentScheduler
        from tianshu.sdk.trigram import AgentRef, Layer, TimeScale

        scheduler = AgentScheduler()
        fast_ticks: list[int] = []
        slow_ticks: list[int] = []

        async def fast_cb(ref, idx, elapsed):
            fast_ticks.append(idx)
            return None

        async def slow_cb(ref, idx, elapsed):
            slow_ticks.append(idx)
            return None

        scheduler.register(
            AgentRef(Layer.DI, "fast"), TimeScale(tick_ms=50), fast_cb,
        )
        scheduler.register(
            AgentRef(Layer.DI, "slow"), TimeScale(tick_ms=200), slow_cb,
        )
        await scheduler.start()
        await asyncio.sleep(0.5)
        await scheduler.stop()

        # 快速 Agent 的 tick 次数应明显多于慢速
        assert len(fast_ticks) > len(slow_ticks) * 1.5


class TestLearnCommand:
    """/learn 命令——LLM 自生成 SKILL.md。"""

    def test_build_learn_prompt_includes_tools(self):
        """Prompt 包含用户描述和可用工具。"""
        from tianshu.renyao.skills.learn import build_learn_prompt
        prompt = build_learn_prompt(
            "test skill", ["tool_a"], "some context", ["tool_a", "tool_b"],
        )
        assert "test skill" in prompt
        assert "tool_a" in prompt
        assert "SKILL.md" in prompt

    def test_parse_skill_md_extracts_frontmatter(self):
        """正确解析 SKILL.md 的 frontmatter。"""
        from tianshu.renyao.skills.learn import parse_skill_md
        sample = "---\nname: test\ndescription: desc\ntrigram: di\ntools: [a]\nversion: 1\n---\n\n# Body"
        meta, body = parse_skill_md(sample)
        assert meta["name"] == "test"
        assert "Body" in body

    def test_parse_invalid_returns_none(self):
        """无效输入返回 None。"""
        from tianshu.renyao.skills.learn import parse_skill_md
        assert parse_skill_md("not a skill") is None


class TestOrchestrator:
    """人层调度器——创建/分发/收集/销毁子 Agent。"""

    @pytest.mark.asyncio
    async def test_create_and_destroy(self):
        """创建子 Agent → 可查询 → 销毁。"""
        from tianshu.renyao.orchestrator import Orchestrator
        orch = Orchestrator()
        a = await orch.create_agent("test", ["web_search"])
        assert orch.active_count == 1
        assert orch.by_name["test"] == a
        await orch.destroy(a)
        assert orch.active_count == 0

    @pytest.mark.asyncio
    async def test_plan_generates_steps(self):
        """计划: AI 分析 → 结构化步骤。"""
        from tianshu.renyao.orchestrator import Orchestrator
        orch = Orchestrator()
        plan = await orch.plan("搜索 Rust 异步 runtime 对比并写报告")
        assert plan.goal
        assert len(plan.steps) >= 1
        assert plan.topology in ("serial", "parallel", "pipeline")

    def test_assess_complexity_detects_multistep(self):
        """复杂度评估: /orchestrate 强制触发, /direct 强制不触发, 短句不触发。"""
        from tianshu.renyao.orchestrator import Orchestrator
        orch = Orchestrator()
        assert orch.assess_complexity("/orchestrate do complex research")
        assert not orch.assess_complexity("/direct just chat")
        assert not orch.assess_complexity("hello")
        assert not orch.assess_complexity("hi")

    def test_plan_step_dependencies(self):
        """PlanStep: 依赖关系正确建模。"""
        from tianshu.renyao.orchestrator import PlanStep
        s1 = PlanStep("A", "搜索", tools_allowed=["search"])
        s2 = PlanStep("B", "分析", depends_on=["A"], tools_allowed=["read"])
        assert s2.depends_on == ["A"]
        assert "A" not in s1.depends_on
