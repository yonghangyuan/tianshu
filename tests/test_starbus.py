"""星群消息总线测试 — Agent 间直接通信 (P2-006)。

覆盖: 点对点/TTL/广播/记忆板/工具注册/contextvar 身份/辩论投票
（用 mock LLM 核心，无真实模型调用）。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from tianshu.core.tool_registry import ToolRegistry
from tianshu.renyao.comm import (
    BusMessage, StarBus, current_agent, set_current_agent, reset_current_agent,
)
from tianshu.renyao.orchestrator import Orchestrator


# ── 总线本体 ───────────────────────────────────────────────────────────────

class TestStarBus:
    def test_send_and_inbox(self):
        bus = StarBus()
        bus.send("searcher", "analyst", "找到3篇文献", {"refs": [1, 2, 3]})
        assert bus.unread_count("analyst") == 1
        msgs = bus.inbox("analyst")
        assert msgs[0].source == "searcher"
        assert msgs[0].intent == "找到3篇文献"
        assert msgs[0].payload["refs"] == [1, 2, 3]

    def test_pop_all_clears_inbox(self):
        bus = StarBus()
        bus.send("a", "b", "m1")
        popped = bus.pop_all("b")
        assert len(popped) == 1
        assert bus.unread_count("b") == 0

    def test_ttl_expiry_filtered(self):
        bus = StarBus()
        bus.send("a", "b", "过期消息", ttl_ms=10)
        time.sleep(0.03)
        assert bus.unread_count("b") == 0
        # 未过期消息仍可见
        bus.send("a", "b", "新鲜消息", ttl_ms=60_000)
        assert bus.unread_count("b") == 1

    def test_send_requires_target(self):
        bus = StarBus()
        with pytest.raises(ValueError):
            bus.send("a", "", "无目标")

    def test_broadcast_subscribe_publish(self):
        bus = StarBus()
        bus.subscribe("osint", "watcher1")
        bus.subscribe("osint", "watcher2")
        sent = bus.publish("osint", "searcher", "突发事件")
        assert len(sent) == 2
        assert bus.unread_count("watcher1") == 1
        assert bus.unread_count("watcher2") == 1
        # 未订阅者收不到
        assert bus.unread_count("outsider") == 0
        assert bus.subscribers("osint") == ["watcher1", "watcher2"]

    def test_unsubscribe(self):
        bus = StarBus()
        bus.subscribe("t", "x")
        bus.unsubscribe("t", "x")
        assert bus.publish("t", "y", "hi") == []
        assert bus.subscribers("t") == []

    def test_board_post_versioning(self):
        bus = StarBus()
        bus.post("结论", "方案A", "analyst")
        e2 = bus.post("结论", "方案B", "reviewer")
        assert e2.version == 2
        assert e2.value == "方案B"
        assert len(e2.history) == 1
        assert e2.history[0]["value"] == "方案A"
        assert bus.read("结论").source == "reviewer"

    def test_board_keys_and_snapshot(self):
        bus = StarBus()
        bus.post("a:1", "x", "s")
        bus.post("b:2", "y", "s")
        assert bus.keys("a:") == ["a:1"]
        assert len(bus.board_snapshot()) == 2

    def test_clear_agent(self):
        bus = StarBus()
        bus.send("a", "b", "m")
        bus.subscribe("t", "b")
        bus.clear_agent("b")
        assert bus.unread_count("b") == 0
        assert bus.subscribers("t") == []

    def test_stats(self):
        bus = StarBus()
        bus.send("a", "b", "m")
        bus.post("k", "v", "a")
        st = bus.stats()
        assert st["total_messages"] == 1
        assert st["board_keys"] == 1
        assert st["inboxes"]["b"] == 1

    def test_inbox_cap(self):
        bus = StarBus(max_inbox=3)
        for i in range(5):
            bus.send("a", "b", f"m{i}")
        assert bus.unread_count("b") == 3


# ── 通信工具注册 + contextvar 身份 ─────────────────────────────────────────

class FakeCore:
    """Mock AgentCore — 记录请求并按 Agent 名返回预设回复。"""

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.captured = []
        self._tool_registry = ToolRegistry()

    async def run(self, req, ctx):
        self.captured.append(req)
        name = "unknown"
        if "[子Agent: " in req.input:
            name = req.input.split("[子Agent: ")[1].split("]")[0]
        content = self.responses.get(name, "默认回复")
        return SimpleNamespace(
            content=content, error="", decision_id="d1", tool_calls=[],
            audit_level=1, model_used="mock", elapsed_ms=1,
        )


@pytest.fixture
def orch():
    core = FakeCore()
    o = Orchestrator(core)
    o.setup(core)
    return o


class TestCommTools:
    def test_tools_registered(self, orch):
        reg = orch.core._tool_registry
        names = [t.name for t in reg._tools.values() if t.skill_name == "starbus"]
        assert set(names) == {"send_message", "read_inbox", "read_board", "post_board"}

    def test_send_message_attributes_source(self, orch):
        token = set_current_agent("searcher")
        try:
            out = asyncio.run(orch._tool_send_message("analyst", "我有发现"))
        finally:
            reset_current_agent(token)
        assert "已发送" in out
        msgs = orch.bus.inbox("analyst")
        assert msgs[0].source == "searcher"

    def test_contextvar_isolation_under_concurrency(self, orch):
        """并行执行时各 Agent 身份互不串扰。"""
        async def worker(name, target):
            token = set_current_agent(name)
            try:
                await orch._tool_send_message(target, f"来自 {name}")
            finally:
                reset_current_agent(token)

        async def main():
            await asyncio.gather(worker("a1", "hub"), worker("a2", "hub"))

        asyncio.run(main())
        sources = {m.source for m in orch.bus.inbox("hub")}
        assert sources == {"a1", "a2"}

    def test_read_inbox_empty_and_full(self, orch):
        assert "空" in asyncio.run(orch._tool_read_inbox())
        orch.bus.send("x", "unknown", "hi")  # current_agent 默认 unknown
        out = asyncio.run(orch._tool_read_inbox())
        assert "[x]" in out

    def test_board_tools_roundtrip(self, orch):
        token = set_current_agent("analyst")
        try:
            asyncio.run(orch._tool_post_board("结论", "方案A"))
            out = asyncio.run(orch._tool_read_board("结论"))
        finally:
            reset_current_agent(token)
        assert "方案A" in out
        assert "analyst" in out

    def test_setup_idempotent_on_reload(self, orch):
        # reload 场景: setup 再调一次 → 不产生重复注册
        orch.setup(orch.core)
        reg = orch.core._tool_registry
        count = sum(1 for t in reg._tools.values() if t.skill_name == "starbus")
        assert count == 4


# ── dispatch 上下文注入 ────────────────────────────────────────────────────

class TestDispatchContext:
    def test_inbox_injected_into_task(self, orch):
        orch.bus.send("reviewer", "analyst", "请核对数据源")
        agent = asyncio.run(orch.create_agent("analyst", []))
        msg = asyncio.run(orch.dispatch(agent, "分析数据"))
        assert agent.status == "done"
        req = orch.core.captured[-1]
        assert "收件箱" in req.input
        assert "reviewer" in req.input
        assert msg.payload["result"] == "默认回复"

    def test_no_comm_block_when_idle(self, orch):
        agent = asyncio.run(orch.create_agent("quiet", []))
        asyncio.run(orch.dispatch(agent, "干活"))
        req = orch.core.captured[-1]
        assert "星群通信" not in req.input

    def test_isolation_dir_reaches_llm(self, orch):
        """修复回归: 隔离目录提示此前只进 payload 不进 input。"""
        agent = asyncio.run(orch.create_agent("iso", [], isolate=True))
        asyncio.run(orch.dispatch(agent, "写文件"))
        req = orch.core.captured[-1]
        assert "工作目录已隔离" in req.input

    def test_destroy_clears_bus(self, orch):
        orch.bus.send("x", "agent1", "m")
        orch.bus.subscribe("t", "agent1")
        agent = asyncio.run(orch.create_agent("agent1", []))
        asyncio.run(orch.destroy(agent))
        assert orch.bus.unread_count("agent1") == 0
        assert orch.bus.subscribers("t") == []


# ── 辩论 / 投票 ────────────────────────────────────────────────────────────

class TestDebateVote:
    def test_debate_positions_on_board(self):
        core = FakeCore({
            "pro": "VOTE 无关。我认为方案A更优，因为成本低。",
            "con": "我反对。方案B虽然贵但风险小。",
        })
        o = Orchestrator(core)
        o.setup(core)
        result = asyncio.run(o.debate(
            "方案A还是方案B", [
                {"name": "pro", "position": "支持A"},
                {"name": "con", "position": "支持B"},
            ],
            rounds=2,
        ))
        assert set(result["positions"].keys()) == {"pro", "con"}
        assert "方案A" in result["positions"]["pro"]
        # 第 2 轮能看到记忆板（输入含其他 Agent 陈述的注入块）
        assert any("共享记忆板" in r.input for r in core.captured)

    def test_vote_tally_and_winner(self):
        core = FakeCore({
            "v1": "VOTE: 方案A\n成本低",
            "v2": "VOTE: 方案A\n同意",
            "v3": "VOTE: 方案B\n风险小",
        })
        o = Orchestrator(core)
        o.setup(core)
        result = asyncio.run(o.vote(
            "选哪个方案", ["方案A", "方案B"],
            [{"name": "v1"}, {"name": "v2"}, {"name": "v3"}],
        ))
        assert result["tally"] == {"方案A": 2, "方案B": 1}
        assert result["winner"] == "方案A"

    def test_vote_invalid_choice_abstains(self):
        core = FakeCore({"v1": "我觉得都不错，弃权"})
        o = Orchestrator(core)
        o.setup(core)
        result = asyncio.run(o.vote(
            "问题", ["A", "B"], [{"name": "v1"}],
        ))
        assert result["winner"] == ""
        assert result["details"][0]["choice"] == "弃权"
