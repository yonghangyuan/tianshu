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
