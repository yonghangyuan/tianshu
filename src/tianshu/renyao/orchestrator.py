"""人层调度器 (Orchestrator) — 动态创建、协调、销毁子 Agent。

调度器是用户与多 Agent 系统之间的唯一对话入口。
它理解意图 → 分解任务 → 组建团队 → 分配工作 → 汇总结果。
子 Agent 之间不直接通信——全部经过调度器中转，确保天层可审计。

用法:
    orch = Orchestrator(core)
    result = await orch.handle("帮我研究 Rust 异步 runtime", ctx)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..sdk.trigram import (
    AgentRef, Layer, TrigramMessage, MessagePriority,
    AgentRegistration, TimeScale, WorldLevel, LayerPermission,
    AuditSixQuestions, DecisionContext,
)
from ..tianyao.agent_scheduler import AgentScheduler, TickCallback


# ═══════════════════════════════════════════════════════════════════════════
# 数据类型
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SubAgent:
    """调度器创建的子 Agent——轻量级，任务完成即销毁。"""

    agent_id: str
    name: str                    # "searcher", "analyst", "writer"
    ref: AgentRef
    skills: list[str]            # 允许使用的工具名
    model: str                   # 底模型
    time_scale: TimeScale
    world_level: WorldLevel = WorldLevel.MEASURABLE
    status: str = "idle"         # idle | busy | done | error
    created_at: float = 0.0
    task_history: list[dict] = field(default_factory=list)
    parent_decision_id: str = ""

    @property
    def is_active(self) -> bool:
        return self.status in ("idle", "busy")


@dataclass
class PlanStep:
    """计划中的一个步骤——哪个 Agent 做什么、依赖谁。"""

    agent_name: str              # 执行 Agent 的名字
    task: str                    # 做什么
    depends_on: list[str] = field(default_factory=list)  # 依赖的 agent_name 列表
    tools_allowed: list[str] = field(default_factory=list)


@dataclass
class OrchestrationPlan:
    """调度器的执行计划——AI 分析用户意图后生成。"""

    goal: str                    # 用户原始目标
    steps: list[PlanStep] = field(default_factory=list)
    topology: str = "serial"     # serial | parallel | pipeline
    agents: dict[str, SubAgent] = field(default_factory=dict)
    decision_id: str = ""

    def summary(self) -> str:
        lines = [f"目标: {self.goal}", f"拓朴: {self.topology}"]
        for i, s in enumerate(self.steps):
            deps = f" (依赖: {', '.join(s.depends_on)})" if s.depends_on else ""
            lines.append(f"  [{i+1}] {s.agent_name}: {s.task}{deps}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 调度器
# ═══════════════════════════════════════════════════════════════════════════

class Orchestrator:
    """人层调度器——天枢多 Agent 协作的中枢。"""

    def __init__(self, core: Any = None):
        self.core = core  # AgentCore 实例（setup 后注入）
        self.active: dict[str, SubAgent] = {}       # agent_id → SubAgent
        self.by_name: dict[str, SubAgent] = {}      # name → SubAgent
        self.scheduler = AgentScheduler()
        self._ready = False

    # ── 初始化 ─────────────────────────────────────────────────────

    def setup(self, core: Any) -> None:
        """注入 AgentCore 实例。"""
        self.core = core
        self._ready = True

    # ── 创建 ───────────────────────────────────────────────────────

    async def create_agent(
        self,
        name: str,
        skills: list[str],
        model: str = "deepseek-v4-flash",
        *,
        world_level: WorldLevel = WorldLevel.MEASURABLE,
        time_scale: TimeScale | None = None,
        isolate: bool = True,
    ) -> SubAgent:
        """创建一个子 Agent。

        Args:
            name: 人类可读名称
            skills: 允许的工具名列表
            model: 底模型
            world_level: 认知层级
            time_scale: 生命节奏
            isolate: True=创建隔离工作目录, 防止子Agent污染主工作区
        """
        agent_id = f"sub_{uuid.uuid4().hex[:8]}"
        ref = AgentRef(Layer.DI, f"subagent:{name}", instance_id=agent_id)

        if time_scale is None:
            time_scale = TimeScale(tick_ms=1000)

        # Worker isolation (#13): 子Agent的写操作限在临时目录
        work_dir = ""
        if isolate:
            import tempfile
            work_dir = tempfile.mkdtemp(prefix=f"tianshu_agent_{name}_")
            # 将隔离目录路径写入 agent 元数据, dispatch 时使用
            skills = [f"isolation_dir={work_dir}"] + skills

        agent = SubAgent(
            agent_id=agent_id,
            name=name,
            ref=ref,
            skills=skills,
            model=model,
            time_scale=time_scale,
            world_level=world_level,
            created_at=time.time(),
        )
        self.active[agent_id] = agent
        self.by_name[name] = agent
        return agent

    # ── 执行 ───────────────────────────────────────────────────────

    async def dispatch(
        self,
        agent: SubAgent,
        task: str,
        *,
        deps: list[str] | None = None,
    ) -> TrigramMessage:
        """给子 Agent 派一个任务。

        如果指定了 deps（依赖的其他 agent_name），会先等待它们完成再执行。
        """
        # 等待依赖
        if deps:
            for dep_name in deps:
                dep = self.by_name.get(dep_name)
                if dep and dep.status == "busy":
                    # 轮询等待
                    for _ in range(120):  # 最多等 2 分钟
                        if dep.status in ("done", "error"):
                            break
                        await asyncio.sleep(1.0)

        agent.status = "busy"

        # Worker隔离: 注入隔离目录到任务描述
        task_with_isolation = task
        if agent.skills and agent.skills[0].startswith("isolation_dir="):
            iso_dir = agent.skills[0].split("=", 1)[1]
            task_with_isolation = (
                f"⚠️ 你的工作目录已隔离: {iso_dir}\n"
                f"所有文件写入必须在此目录下进行。不要写主工作区。\n\n"
                f"{task}"
            )

        msg = TrigramMessage.create(
            source=AgentRef(Layer.REN, "orchestrator"),
            target=agent.ref,
            intent=task,
            payload={
                "task": task_with_isolation,
                "tools_allowed": agent.skills,
                "model": agent.model,
            },
            priority=MessagePriority.NORMAL,
        )
        agent.task_history.append(msg.to_dict())

        # 调用 AgentCore 执行任务
        if self.core and self._ready:
            try:
                from ..sdk.models import AgentRequest, AgentContext
                ctx = AgentContext(session_id=agent.agent_id)
                req = AgentRequest(
                    input=f"[子Agent: {agent.name}]\n任务: {task}\n可用工具: {', '.join(agent.skills)}",
                    task_type="conversation",
                    model_override=agent.model,
                )
                resp = await self.core.run(req, ctx)
                agent.status = "done" if resp.error == "" else "error"
                if resp.content:
                    # 构造结果消息
                    return TrigramMessage.create(
                        source=agent.ref,
                        target=AgentRef(Layer.REN, "orchestrator"),
                        intent=f"{agent.name} 完成任务",
                        payload={"result": resp.content, "status": agent.status},
                    )
            except Exception as e:
                agent.status = "error"
                # Memory hook: 记录子 Agent 失败
                if self.core and hasattr(self.core, 'memory'):
                    await self.core.memory.provider.on_delegation(
                        agent.name, task, f"ERROR: {e}",
                        decision_id=agent.agent_id,
                    )
                return TrigramMessage.create(
                    source=agent.ref,
                    target=AgentRef(Layer.REN, "orchestrator"),
                    intent=f"{agent.name} 执行失败: {e}",
                    payload={"error": str(e)},
                )

        agent.status = "error"
        return TrigramMessage.create(
            source=agent.ref,
            target=AgentRef(Layer.REN, "orchestrator"),
            intent=f"{agent.name}: 调度器未就绪或无 AgentCore",
        )

    # ── 收集 ──────────────────────────────────────────────────────

    async def collect(
        self,
        agents: list[SubAgent] | None = None,
        timeout: float = 120.0,
    ) -> dict[str, str]:
        """等待并收集中间结果。

        Returns:
            {agent_name: result_text}
        """
        target = agents if agents is not None else list(self.active.values())
        results: dict[str, str] = {}
        t0 = time.time()

        while len(results) < len(target):
            elapsed = time.time() - t0
            if elapsed > timeout:
                break
            for agent in target:
                if agent.name in results:
                    continue
                if agent.status == "done" and agent.task_history:
                    last = agent.task_history[-1]
                    results[agent.name] = last.get("intent", "")
                elif agent.status == "error":
                    results[agent.name] = f"[ERROR: {agent.name}]"
            if len(results) < len(target):
                await asyncio.sleep(0.5)

        return results

    # ── 并行编排 (#14) ──────────────────────────────────────────

    async def execute_parallel(
        self,
        tasks: list[tuple[str, str, list[str]]],
        model: str = "deepseek-v4-flash",
    ) -> dict[str, str]:
        """并行执行多个子Agent任务。

        Args:
            tasks: [(agent_name, task_description, skills), ...]

        Returns:
            {agent_name: result_text}
        """
        import asyncio as _aio

        async def _run_one(name, desc, skills):
            agent = await self.create_agent(name, skills, model)
            msg = await self.dispatch(agent, desc)
            result = msg.payload.get("result", str(msg.intent)[:500]) if msg.payload else ""
            return name, result

        coros = [_run_one(n, d, s) for n, d, s in tasks]
        results = await _aio.gather(*coros, return_exceptions=True)

        output: dict[str, str] = {}
        for r in results:
            if isinstance(r, Exception):
                output["error"] = str(r)
            else:
                output[r[0]] = r[1]
        return output

    # ── Adversarial verify (#15) ────────────────────────────────

    async def verify(
        self,
        claim: str,
        verifier_count: int = 2,
    ) -> dict:
        """调度多个Agent独立验证同一个主张。

        Args:
            claim: 需要验证的主张
            verifier_count: 验证者数量

        Returns:
            {"verified": bool, "votes": [{agent, verdict, reason}]}
        """
        votes = []
        for i in range(verifier_count):
            agent = await self.create_agent(
                f"verifier_{i}", ["web_search", "read_file"],
            )
            task = (
                f"验证以下主张是否为真。默认立场: 怀疑——尽量证伪。\n"
                f"主张: {claim}\n"
                f"输出格式: VERIFIED 或 REFUTED, 然后给出理由。"
            )
            msg = await self.dispatch(agent, task)
            result = msg.payload.get("result", "") if msg.payload else ""
            verdict = "REFUTED" if "REFUTED" in result.upper() else "VERIFIED"
            votes.append({"agent": agent.name, "verdict": verdict, "reason": result[:200]})
            await self.destroy(agent)

        verified_count = sum(1 for v in votes if v["verdict"] == "VERIFIED")
        return {
            "verified": verified_count > verifier_count // 2,
            "votes": votes,
        }

    # ── 销毁 ──────────────────────────────────────────────────────

    async def destroy(self, agent: SubAgent) -> bool:
        """销毁子 Agent，释放资源并清理隔离目录。"""
        # 清理隔离目录
        if agent.skills and agent.skills[0].startswith("isolation_dir="):
            import shutil as _shutil
            iso_dir = agent.skills[0].split("=", 1)[1]
            try:
                _shutil.rmtree(iso_dir, ignore_errors=True)
            except Exception:
                pass

        agent_id = agent.agent_id
        self.scheduler.unregister(agent.ref)
        self.by_name.pop(agent.name, None)
        removed = self.active.pop(agent_id, None)
        return removed is not None

    # ── 计划（仅分析，不执行）────────────────────────────────────

    async def plan(self, user_input: str) -> OrchestrationPlan:
        """AI 分析任务并生成计划，不执行。

        用轻量级模型快速分析用户意图，输出结构化计划。
        """
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        # 构建一个简单的计划分析 prompt
        plan_prompt = (
            f"分析以下用户请求，拆解为多 Agent 协作步骤。\n\n"
            f"用户请求: {user_input}\n\n"
            f"可用的 Agent 类型和技能:\n"
            f"  - searcher: web_search (搜索网页)\n"
            f"  - analyst: web_search, read_file (深度分析)\n"
            f"  - writer: write_file, write_docx (生成文件)\n"
            f"  - translator: translate (翻译)\n"
            f"  - coder: shell_exec, write_file (代码编写)\n"
            f"  - reviewer: read_file (代码审查)\n\n"
            f"输出 JSON 格式:\n"
            f'{{"goal": "...", "topology": "serial|parallel|pipeline", '
            f'"steps": [{{"agent": "searcher", "task": "...", '
            f'"depends_on": [], "tools": ["web_search"]}}]}}\n'
        )

        if self.core and self._ready:
            try:
                from ..sdk.models import AgentRequest, AgentContext
                ctx = AgentContext(session_id=plan_id)
                req = AgentRequest(
                    input=plan_prompt,
                    task_type="planning",
                    model_override="deepseek-v4-pro",
                )
                resp = await self.core.run(req, ctx)
                if resp and resp.content:
                    return self._parse_plan(resp.content, user_input)
            except Exception:
                pass

        # 降级：简单串行默认计划
        return OrchestrationPlan(
            goal=user_input,
            topology="serial",
            steps=[PlanStep(agent_name="agent", task=user_input, tools_allowed=["web_search"])],
            decision_id=plan_id,
        )

    def _parse_plan(self, raw: str, goal: str) -> OrchestrationPlan:
        """从 LLM 回复中提取计划 JSON。"""
        import json, re
        # 尝试提取 JSON 块
        match = re.search(r'\{[^{}]*"goal"[^{}]*\}', raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                steps = [
                    PlanStep(
                        agent_name=s.get("agent", "agent"),
                        task=s.get("task", ""),
                        depends_on=s.get("depends_on", []),
                        tools_allowed=s.get("tools", []),
                    )
                    for s in data.get("steps", [])
                ]
                return OrchestrationPlan(
                    goal=data.get("goal", goal),
                    steps=steps,
                    topology=data.get("topology", "serial"),
                    decision_id=f"plan_{uuid.uuid4().hex[:8]}",
                )
            except json.JSONDecodeError:
                pass

        # 解析失败 → 单步计划
        return OrchestrationPlan(
            goal=goal,
            topology="serial",
            steps=[PlanStep(agent_name="agent", task=goal)],
            decision_id=f"plan_{uuid.uuid4().hex[:8]}",
        )

    # ── 复杂度评估 ──────────────────────────────────────────────

    def assess_complexity(self, user_input: str) -> bool:
        """快速判断是否需要多 Agent 编排。

        启发式规则（后续可升级为 AI 判断）:
          - 包含 "搜索...总结" 或 "分析...写" → 多 Agent
          - 包含 "比较" + "报告" → 多 Agent
          - 长度 < 20 字符的短句 → 单 Agent
          - 包含 /orchestrate → 强制
        """
        if user_input.startswith("/orchestrate"):
            return True
        if user_input.startswith("/direct"):
            return False
        if len(user_input) < 20:
            return False
        # 多步骤关键词
        patterns = [
            ("搜索", "总结"), ("搜索", "分析"), ("搜索", "写"),
            ("分析", "报告"), ("分析", "写"), ("比较", "报告"),
            ("研究", "总结"), ("翻译", "保存"),
            ("search", "analyze"), ("search", "summarize"), ("search", "write"),
            ("analyze", "report"), ("analyze", "write"), ("study", "summarize"),
        ]
        for a, b in patterns:
            if a in user_input and b in user_input:
                return True
        return False

    # ── 状态 ──────────────────────────────────────────────────────

    @property
    def active_count(self) -> int:
        return sum(1 for a in self.active.values() if a.is_active)

    def status_summary(self) -> str:
        lines = [f"活跃子 Agent: {self.active_count}/{len(self.active)}"]
        for a in self.active.values():
            lines.append(f"  [{a.status}] {a.name} ({a.model}) — {len(a.task_history)} tasks")
        return "\n".join(lines)
