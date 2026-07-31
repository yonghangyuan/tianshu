"""Agent Core Service — 天枢的大脑。

封装 Agent Loop + Router + Audit + Skills 为一个统一服务类。
对外暴露单一 run() 接口——CLI / HTTP / 飞书 / 微信 都调同一个方法。

这是"逻辑微服务，物理单体"的核心——如果未来拆进程，
这个类就是 tianshu-core 微服务的主体。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from ..sdk.models import (
    AgentContext, AgentRequest, AgentResponse, AgentTurn,
    AuditLevel, AuditRecord, ProvenanceInput, ProviderResponse,
    ContentDelta, ReasoningDelta, ToolCallStart,
    ToolCallResult, ToolCallConfirm, StreamDone, StreamError,
)
from ..diyao.providers.base import BaseProvider
from ..diyao.providers.registry import ProviderRegistry
from ..renyao.skills.service import SkillService
from ..renyao.skills.plugin import PluginManager
from ..memory.service import MemoryService
from ..tianyao.service import AuditService
from ..tianyao.scheduler import CronScheduler
from .router import ModelRouter, RoutingConfig
from .status import route as _s_route, tool as _s_tool, done as _s_done, error as _s_error


def _classify_error(error: Exception, provider_name: str, model_id: str) -> str:
    """分类 API 错误，返回人类可读的提示。"""
    msg = str(error)
    model_ref = f"{provider_name}/{model_id}"

    # 网络错误
    if any(k in msg.lower() for k in ("connect", "timeout", "resolve", "refused", "network")):
        return (
            f"🌐 网络不通 ({model_ref})\n"
            f"   请检查网络连接、代理设置、或 API 地址是否正确。"
        )
    # 401/403 鉴权错误
    if "401" in msg or "403" in msg or "unauthorized" in msg.lower():
        return (
            f"🔑 API Key 无效 ({model_ref})\n"
            f"   请用 /setup 重新配置 Key，或检查环境变量。"
        )
    # 400 参数错误
    if "400" in msg:
        return (
            f"⚠️ 请求格式错误 ({model_ref})\n"
            f"   {msg[:200]}"
        )
    # 429 限流
    if "429" in msg or "rate" in msg.lower():
        return (
            f"⏳ 请求太频繁 ({model_ref})\n"
            f"   稍后自动重试。如果持续出现，请降低使用频率。"
        )
    # 上下文太长
    if "context" in msg.lower() or "token" in msg.lower() or "maximum" in msg.lower():
        return (
            f"📏 上下文超长 ({model_ref})\n"
            f"   对话历史已自动压缩，请重试。"
        )
    # 模型过载/不可用
    if "overload" in msg.lower() or "503" in msg or "unavailable" in msg.lower():
        return (
            f"🏗️ 模型繁忙 ({model_ref})\n"
            f"   服务器过载，稍后自动重试。"
        )
    # 未知错误（含空白消息兜底——至少显示异常类型）
    if msg.strip():
        detail = msg[:300]
    else:
        detail = f"{type(error).__name__}"
        # httpx 异常额外提取状态码
        if hasattr(error, 'response') and hasattr(error.response, 'status_code'):
            detail += f"(HTTP {error.response.status_code})"
    return f"❌ {model_ref}: {detail}"


class AgentCore:
    """Agent Core Service — 无状态的 Agent 执行引擎。

    用法:
        core = AgentCore()
        core.setup(registry, routing_config, system_prompt)
        response = await core.run(AgentRequest(input="你好"))
    """

    def __init__(self) -> None:
        # 子系统（通过 setup() 注入）
        self._registry: ProviderRegistry | None = None
        self._router: ModelRouter | None = None
        self._audit = AuditService("tianshu.db")
        self._skills = SkillService()
        self._memory = MemoryService()
        self._plugins = PluginManager()
        self._cron = CronScheduler()
        self._system_prompt = ""

        # 标记
        self._ready = False
        self._last_reasoning = ""  # 最近一次推理过程（/think 命令查看）
        self._confirm_allowed = False
        self._confirm_pending: Any = None
        self._tool_registry: Any = None  # ToolRegistry
        self._mode: str = "normal"       # normal / plan / auto
        self._policy_engine: Any = None  # PolicyEngine

    # ── 初始化 ───────────────────────────────────────────────────────

    def setup(
        self,
        registry: ProviderRegistry,
        routing: RoutingConfig,
        system_prompt: str = "",
        db_path: str = "tianshu.db",
        skill_discover: bool = True,
    ) -> None:
        self._registry = registry
        self._router = ModelRouter(routing, registry)
        self._audit = AuditService(db_path)
        self._system_prompt = system_prompt

        if skill_discover:
            self._skills.discover_and_load()

        # 工具注册中心
        from .tool_registry import ToolRegistry, ToolInfo
        self._tool_registry = ToolRegistry()
        self._tool_registry.scan_skills(self._skills.loader)

        # 策略引擎（天爻）
        from .policy_engine import PolicyEngine
        from pathlib import Path as _Path
        self._policy_engine = PolicyEngine()
        for candidate in ["config/policy.yaml", str(_Path(__file__).parent.parent.parent.parent / "config" / "policy.yaml")]:
            if _Path(candidate).exists():
                self._policy_engine.load(candidate)
                break

        # 注册内置工具
        for ti in [
            ToolInfo("get_model_status", "View registered AI models.",
                     {"type": "object", "properties": {}, "required": []},
                     None, permission=0, skill_name="core"),
            ToolInfo("remember_fact", "Save a fact to long-term memory.",
                     {"type": "object", "properties": {
                         "key": {"type": "string"}, "value": {"type": "string"},
                         "category": {"type": "string", "default": "fact"},
                     }, "required": ["key", "value"]},
                     None, permission=0, skill_name="core"),
            ToolInfo("recall_memory", "Search long-term memory.",
                     {"type": "object", "properties": {
                         "query": {"type": "string"},
                     }, "required": ["query"]},
                     None, permission=0, skill_name="core"),
        ]:
            self._tool_registry.register(ti)

        # 加载 Plugins + Cron
        self._plugins.load_all()
        self._cron.load()

        self._ready = True

    # ── 属性 ─────────────────────────────────────────────────────────

    @property
    def router(self) -> ModelRouter:
        if not self._router:
            raise RuntimeError("AgentCore not set up. Call setup() first.")
        return self._router

    @property
    def skills(self) -> SkillService:
        return self._skills

    @property
    def memory(self) -> MemoryService:
        return self._memory

    @property
    def audit(self) -> AuditService:
        return self._audit

    @property
    def plugins(self) -> PluginManager:
        return self._plugins

    @property
    def cron(self) -> CronScheduler:
        return self._cron

    @property
    def last_reasoning(self) -> str:
        return self._last_reasoning

    @property
    def model_count(self) -> int:
        return len(self._registry.list_all()) if self._registry else 0

    # ── 核心 API ────────────────────────────────────────────────────

    async def run(
        self,
        request: AgentRequest,
        ctx: AgentContext | None = None,
    ) -> AgentResponse:
        """执行一次 Agent 对话。

        Args:
            request: 用户请求
            ctx: 会话上下文（None 则创建新会话）

        Returns:
            标准化的 AgentResponse
        """
        if not self._ready:
            return AgentResponse(error="AgentCore not set up. Call setup() first.")

        t0 = time.time()

        if ctx is None:
            ctx = AgentContext(
                session_id=request.session_id or f"sess_{int(time.time())}"
            )

        # 1. 审计：分配 ID + 快照
        decision_id = self._audit.generate_id()
        snapshot = self._audit.capture_snapshot()

        # 2. 路由：session override > rules
        override = request.model_override or ctx.metadata.get("model_override", "")
        if override:
            provider_name, _, model_short = override.partition("/")
            provider, model_id, level = self.router.route_direct(
                provider_name, model_short or provider_name
            )
        else:
            provider, model_id, level = await self.router.route(request.task_type)

        if provider is None:
            _s_error(f"No model for {request.task_type}")
            return AgentResponse(
                decision_id=decision_id,
                error=f"No model available for task_type={request.task_type}",
            )

        _s_route(f"{provider.provider_name}/{model_id}", int((time.time()-t0)*1000))

        # 3. 检测"继续"——增强上下文
        user_input = request.input
        if user_input.strip() in ("继续", "continue", "接着", "go on"):
            # 找到上次工具调用的占位消息，替换为有效提示
            for i in range(len(ctx.messages) - 1, -1, -1):
                m = ctx.messages[i]
                if m.get("role") == "assistant" and "输入'继续'" in str(m.get("content", "")):
                    ctx.messages[i] = {
                        "role": "user",
                        "content": (
                            "请基于上述工具调用的结果，用中文给出完整回答。"
                            "总结你找到了什么信息，并回答用户最初的问题。"
                        ),
                    }
                    break

        # 4. 构建消息（含上下文压缩）
        provider_info = f"{provider.provider_name}/{model_id}"
        messages = await self._build_messages(
            user_input, ctx, level, provider_info, provider
        )

        # 4. ReAct 循环
        tool_results: list[dict[str, Any]] = []
        reasoning: list[str] = []
        final_content = ""
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for _ in range(10):  # MAX_TOOL_ROUNDS
            try:
                resp = await provider.chat(
                    messages=messages,
                    tools=self._get_tools(),
                )
            except Exception as e:
                return AgentResponse(
                    decision_id=decision_id,
                    error=f"{provider.provider_name}/{model_id}: {e}",
                    elapsed_ms=int((time.time()-t0)*1000),
                )

            if resp.content:
                reasoning.append(resp.content)
            if getattr(resp, "reasoning_content", ""):
                self._last_reasoning = resp.reasoning_content
            total_prompt_tokens += resp.usage.prompt_tokens
            total_completion_tokens += resp.usage.completion_tokens

            # 无工具调用 → 最终回复
            if not resp.tool_calls:
                final_content = resp.content or ""
                break

            # 有 tools

            # 提取 reasoning_content（DS v4）
            rc = ""
            if resp.raw:
                rc = (
                    resp.raw.get("choices", [{}])[0]
                    .get("message", {})
                    .get("reasoning_content", "")
                )

            # 执行工具
            for tc in resp.tool_calls:
                t0_tool = time.time()
                try:
                    output = await self._execute_tool(tc.name, tc.arguments)
                    success = True
                    result_text = str(output)
                except Exception as e:
                    output = str(e)
                    success = False
                    result_text = f"Error: {e}"
                _s_tool(tc.name, result_text[:60], int((time.time()-t0_tool)*1000))

                tool_results.append({
                    "name": tc.name,
                    "success": success,
                    "output": result_text[:500],
                })

                # 反馈给 LLM
                asst: dict[str, Any] = {
                    "role": "assistant",
                    "content": resp.content or "",
                    "tool_calls": [{
                        "id": tc.id, "type": "function",
                        "function": {"name": tc.name, "arguments": str(tc.arguments)},
                    }],
                }
                if rc:
                    asst["reasoning_content"] = rc
                messages.append(asst)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text[:2000],
                })

        # 5. 审计记录
        record = AuditRecord(
            decision_id=decision_id,
            timestamp=time.time(),
            llm_model=f"{provider.provider_name}/{model_id}",
            reasoning_chain=reasoning,
            input_data=snapshot,
            level=int(level),
            session_id=ctx.session_id,
            task_type=request.task_type,
        )
        asyncio.ensure_future(self._audit.record(record))

        # 6. 观测 + 记忆 + 自进化 + L4 画像
        self._skills.observe_turn(request.input)
        self._evolution_counter = getattr(self, '_evolution_counter', 0) + 1
        if self._evolution_counter % 5 == 0:
            new_skills = await self._skills.evolve_skills(provider)
            if new_skills:
                import sys
                sys.stderr.write(f"  🧬 自进化: {len(new_skills)} 个新 Skill 已生成\n")
                sys.stderr.flush()

        if final_content:
            # L4: 自动用户画像
            try:
                profile_items = await self._memory.auto_profile(
                    request.input, final_content, provider
                )
                if profile_items:
                    import sys
                    sys.stderr.write(
                        f"  🧠 L4画像: {len(profile_items)} 条新结论\n"
                    )
                    sys.stderr.flush()
            except Exception:
                pass
            await self._memory.remember(
                key=f"conv_{decision_id}",
                value=f"Q: {request.input[:100]}\nA: {final_content[:200]}",
                category="conversation",
                session_id=ctx.session_id,
            )

        # 7. 更新上下文
        ctx.messages.append({"role": "user", "content": request.input})
        if final_content:
            ctx.messages.append({"role": "assistant", "content": final_content})

        elapsed = int((time.time() - t0) * 1000)
        _s_done(decision_id, f"{provider.provider_name}/{model_id}", len(tool_results), elapsed)

        return AgentResponse(
            decision_id=decision_id,
            content=final_content,
            tool_calls=tool_results,
            audit_level=int(level),
            model_used=f"{provider.provider_name}/{model_id}",
            elapsed_ms=elapsed,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
        )

    async def run_stream(
        self,
        request: AgentRequest,
        ctx: AgentContext | None = None,
    ):
        """流式执行一次 Agent 对话 —— 逐事件 yield。

        与 run() 共享相同的路由/审计/记忆逻辑，但 LLM 调用走
        chat_stream()，每次增量文本即时 yield 给调用方。

        Yields:
            ContentDelta | ReasoningDelta | ToolCallStart |
            ToolCallResult | ToolCallConfirm | StreamDone | StreamError
        """
        from collections.abc import AsyncGenerator

        if not self._ready:
            yield StreamError(message="AgentCore not set up. Call setup() first.")
            return

        t0 = time.time()

        if ctx is None:
            ctx = AgentContext(
                session_id=request.session_id or f"sess_{int(time.time())}"
            )

        # 1. 审计：分配 ID + 快照
        decision_id = self._audit.generate_id()
        snapshot = self._audit.capture_snapshot()

        # 2. 路由
        override = request.model_override or ctx.metadata.get("model_override", "")
        if override:
            provider_name, _, model_short = override.partition("/")
            provider, model_id, level = self.router.route_direct(
                provider_name, model_short or provider_name
            )
        else:
            provider, model_id, level = await self.router.route(request.task_type)

        if provider is None:
            yield StreamError(
                message=f"No model available for task_type={request.task_type}",
                decision_id=decision_id,
            )
            return

        _s_route(f"{provider.provider_name}/{model_id}", int((time.time() - t0) * 1000))

        # 3. 构建消息
        provider_info = f"{provider.provider_name}/{model_id}"
        messages = await self._build_messages(
            request.input, ctx, level, provider_info, provider
        )

        # 3.4 记忆：预取 + 触发权重提升
        memory_context = await self._memory.prefetch(request.input)
        if memory_context:
            messages.append({"role": "system", "content": memory_context})
            # 提升被召回记忆的权重
            for line in memory_context.split("\n"):
                if "]: " in line:
                    key = line.split("]: ")[0].split("] ")[-1].strip()
                    if key:
                        await self._memory.boost(key)

        # 每 10 轮触发记忆衰减+压缩
        self._turn_counter = getattr(self, '_turn_counter', 0) + 1
        if self._turn_counter % 10 == 0:
            asyncio.ensure_future(self._memory.maybe_compress(provider))

        # 3.5 Plan Mode / /plan 命令: 生成执行计划
        _plan = None
        force_plan = getattr(self, '_force_plan', False)
        self._force_plan = False
        if self._mode == "plan" or force_plan:
            from .planner import build_planner_prompt, parse_plan_from_json, format_plan_ascii
            try:
                plan_resp = await provider.chat(
                    messages=[{"role": "user", "content": build_planner_prompt(request.input)}],
                    max_tokens=1000, temperature=0.3,
                )
                _plan = parse_plan_from_json(plan_resp.content or "")
                # 简单任务（≤2 步）跳过计划，直接对话
                if _plan and len(_plan.steps) <= 2:
                    _plan = None
                elif _plan and _plan.steps:
                    yield ContentDelta(text=f"\n{format_plan_ascii(_plan)}\n")
                    plan_context = (
                        f"[执行计划]\n目标: {_plan.goal}\n"
                        + "\n".join(f"  Step {s.id}: {s.goal}" for s in _plan.steps)
                        + "\n请按步骤执行。每完成一步，告知进度。"
                    )
                    messages.append({"role": "user", "content": plan_context})
            except Exception as e:
                yield ContentDelta(text=f"\n[Plan generation failed: {type(e).__name__}, continuing without plan]\n")

        # 4. ReAct 循环（流式版本）—— Token 预算驱动
        tool_results: list[dict[str, Any]] = []
        reasoning: list[str] = []
        final_content = ""
        total_prompt_tokens = 0
        total_completion_tokens = 0
        tool_count = 0
        _last_tool_signatures: list[str] = []

        # Token 预算：总计 64K tokens（约 DS 上下文窗口的 60%）
        TOKEN_BUDGET = 64000
        _budget_warned = False
        _budget_exhausted = False
        _round = 0

        while not _budget_exhausted and _round < 24:
            _round += 1
            tokens_used = total_prompt_tokens + total_completion_tokens

            # 预算检查
            if tokens_used > TOKEN_BUDGET * 0.7 and not _budget_warned:
                _budget_warned = True
                messages.append({
                    "role": "user",
                    "content": (
                        f"已消耗 {tokens_used // 1000}K tokens。"
                        "请基于现有信息尽快给出回答，只在必要时再搜一次。"
                    ),
                })
            elif tokens_used > TOKEN_BUDGET * 0.9:
                _budget_exhausted = True
                messages.append({
                    "role": "user",
                    "content": f"Token 预算耗尽 ({tokens_used // 1000}K / {TOKEN_BUDGET // 1000}K)。禁止再调工具，直接给出最终回答。",
                })

            round_content = ""
            round_tool_calls: list[dict[str, Any]] = []
            round_reasoning = ""

            try:
                # 最后一轮不传 tools，强制纯文本回复
                tools_for_round = None if _budget_exhausted else self._get_tools()
                stream = provider.chat_stream(
                    messages=messages,
                    tools=tools_for_round,
                )
                async for chunk in stream:
                    # 推理内容
                    if chunk.reasoning_content:
                        round_reasoning += chunk.reasoning_content
                        yield ReasoningDelta(text=chunk.reasoning_content)

                    # 文本内容
                    if chunk.delta_content:
                        round_content += chunk.delta_content
                        yield ContentDelta(text=chunk.delta_content)

                    # 收集 tool_call 增量
                    if chunk.tool_call_deltas:
                        round_tool_calls = chunk.tool_call_deltas

                    # 统计
                    if chunk.usage:
                        total_prompt_tokens += chunk.usage.prompt_tokens
                        total_completion_tokens += chunk.usage.completion_tokens

            except Exception as e:
                error_msg = _classify_error(e, provider.provider_name, model_id)
                yield StreamError(message=error_msg, decision_id=decision_id)
                return

            # 更新 reasoning
            if round_reasoning:
                self._last_reasoning = round_reasoning
            if round_content:
                reasoning.append(round_content)

            # 无工具调用 → 最终回复
            if not round_tool_calls:
                final_content = round_content
                break

            # 有工具调用 → 执行并反馈
            tool_count += len(round_tool_calls)

            # 解析累积后的 tool_calls
            parsed_tool_calls: list[dict[str, Any]] = []
            for tc in round_tool_calls:
                name = tc.get("name", "")
                tc_id = tc.get("id", "")
                args_str = tc.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except Exception:
                    args = {}

                parsed_tool_calls.append({
                    "id": tc_id,
                    "name": name,
                    "arguments": args,
                })

            for tc in parsed_tool_calls:
                name = tc["name"]
                args = tc["arguments"]

                # ── 重复调用检测 ──
                import hashlib, json as _json_dedup
                sig = f"{name}:{_json_dedup.dumps(args, sort_keys=True, ensure_ascii=False)}"
                _last_tool_signatures.append(sig)
                if len(_last_tool_signatures) > 3:
                    _last_tool_signatures.pop(0)
                # 同一签名连续 2 次 → 拒绝，强制 LLM 换方法
                if _last_tool_signatures.count(sig) >= 2:
                    yield ToolCallResult(
                        tool_name=name,
                        success=False,
                        output=(
                            f"⛔ 同一工具({name})相同参数已调用 2 次，均未解决问题。"
                            f"请换完全不同的方法，或告知用户当前卡在哪里。"
                        ),
                        elapsed_ms=0,
                    )
                    tool_results.append({"name": name, "success": False,
                                         "output": "重复调用被拒绝"})
                    continue

                yield ToolCallStart(
                    tool_name=name,
                    tool_args=args,
                    tool_id=tc["id"],
                )

                # ── 权限检查 ──
                # automode: 跳过所有确认
                if getattr(self, '_automode', False):
                    pass  # 直接执行
                else:
                    perm = self._get_tool_permission(name)
                    # 检查白名单（用户选择了 "always allow"）
                    whitelist = getattr(self, '_permission_whitelist', set())
                    if perm >= 2 and name not in whitelist:
                        confirm_event = asyncio.Event()
                        self._confirm_allowed = False
                        self._confirm_pending = confirm_event

                        yield ToolCallConfirm(
                            tool_name=name,
                            tool_args=args,
                            permission_level=perm,
                        )

                        # 等待用户确认（或超时 60s 自动拒绝）
                        try:
                            await asyncio.wait_for(confirm_event.wait(), timeout=60.0)
                        except asyncio.TimeoutError:
                            self._confirm_allowed = False

                        if not self._confirm_allowed:
                            # 用户拒绝 → 返回错误给 LLM
                            result_text = f"User denied tool execution: {name}"
                            yield ToolCallResult(
                                tool_name=name,
                                success=False,
                                output=result_text,
                                elapsed_ms=0,
                            )
                            # 反馈拒绝给 LLM（含 reasoning_content）
                            deny_msg: dict[str, Any] = {
                                "role": "assistant",
                                "content": round_content or "",
                                "tool_calls": [{
                                    "id": tc["id"],
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)},
                                }],
                            }
                            if round_reasoning:
                                deny_msg["reasoning_content"] = round_reasoning
                            messages.append(deny_msg)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": result_text,
                            })
                            tool_results.append({
                                "name": name,
                                "success": False,
                                "output": result_text,
                            })
                            continue

                # ── 天爻策略检查 ──
                if self._policy_engine and self._policy_engine.enabled:
                    decision = self._policy_engine.evaluate(name, args)
                    if decision.action == "deny":
                        yield ToolCallResult(
                            tool_name=name, success=False,
                            output=decision.message or f"策略拒绝: {decision.policy_name}",
                            elapsed_ms=0,
                        )
                        tool_results.append({"name": name, "success": False, "output": f"policy_deny:{decision.policy_name}"})
                        continue
                    elif decision.action == "confirm" and not getattr(self, '_automode', False):
                        confirm_event = asyncio.Event()
                        self._confirm_allowed = False; self._confirm_pending = confirm_event
                        yield ToolCallConfirm(
                            tool_name=name, tool_args=args,
                            permission_level=3,
                        )
                        try:
                            await asyncio.wait_for(confirm_event.wait(), timeout=60)
                        except asyncio.TimeoutError:
                            self._confirm_allowed = False
                        if not self._confirm_allowed:
                            yield ToolCallResult(tool_name=name, success=False,
                                output=f"策略确认被拒: {decision.policy_name}", elapsed_ms=0)
                            continue

                # ── 执行工具 ──
                t0_tool = time.time()
                try:
                    output = await self._execute_tool(name, args)
                    success = True
                    result_text = str(output)
                except Exception as e:
                    output = str(e)
                    success = False
                    result_text = f"Error: {e}"

                elapsed_tool = int((time.time() - t0_tool) * 1000)
                _s_tool(name, result_text[:60], elapsed_tool)

                yield ToolCallResult(
                    tool_name=name,
                    success=success,
                    output=result_text[:500],
                    elapsed_ms=elapsed_tool,
                )

                tool_results.append({
                    "name": name,
                    "success": success,
                    "output": result_text[:500],
                })

                # 反馈给 LLM（DeepSeek v4 thinking 模式要求 reasoning_content）
                asst_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": round_content or "",
                    "tool_calls": [{
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)},
                    }],
                }
                if round_reasoning:
                    asst_msg["reasoning_content"] = round_reasoning
                messages.append(asst_msg)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text[:2000],
                })

        # 5. 审计记录
        record = AuditRecord(
            decision_id=decision_id,
            timestamp=time.time(),
            llm_model=f"{provider.provider_name}/{model_id}",
            reasoning_chain=reasoning,
            input_data=snapshot,
            level=int(level),
            session_id=ctx.session_id,
            task_type=request.task_type,
        )
        asyncio.ensure_future(self._audit.record(record))

        # 6. 观测 + 记忆
        self._skills.observe_turn(request.input)
        self._evolution_counter = getattr(self, '_evolution_counter', 0) + 1
        if self._evolution_counter % 5 == 0:
            new_skills = await self._skills.evolve_skills(provider)
            if new_skills:
                import sys
                sys.stderr.write(f"  🧬 自进化: {len(new_skills)} 个新 Skill 已生成\n")
                sys.stderr.flush()

        if final_content:
            try:
                profile_items = await self._memory.auto_profile(
                    request.input, final_content, provider
                )
                if profile_items:
                    import sys
                    sys.stderr.write(f"  🧠 L4画像: {len(profile_items)} 条新结论\n")
                    sys.stderr.flush()
            except Exception:
                pass
            await self._memory.remember(
                key=f"conv_{decision_id}",
                value=f"Q: {request.input[:100]}\nA: {final_content[:200]}",
                category="conversation",
                session_id=ctx.session_id,
            )
            # 消化：从对话中提取可复用事实
            if len(final_content) > 100:
                facts = await self._memory.digest(
                    request.input, final_content, provider,
                )
                if facts:
                    import sys
                    sys.stderr.write(f"  🧠 记忆: 提取 {len(facts)} 条新事实\n")
                    sys.stderr.flush()

        # 7. 更新上下文——保留工具调用历史让"继续"能用
        ctx.messages.append({"role": "user", "content": request.input})
        # 把 ReAct 循环产生的 assistant+tool 消息写入 ctx
        offset = (1 if self._system_prompt else 0) + len(ctx.messages) - 1
        for m in messages[offset:]:
            ctx.messages.append(m)
        if final_content:
            ctx.messages.append({"role": "assistant", "content": final_content})
        elif tool_count > 0:
            ctx.messages.append({
                "role": "assistant",
                "content": (
                    f"[已通过 {tool_count} 次工具调用收集信息。"
                    f"输入'继续'让我基于已有结果给出答案。]"
                ),
            })

        elapsed = int((time.time() - t0) * 1000)
        _s_done(decision_id, f"{provider.provider_name}/{model_id}", tool_count, elapsed)

        yield StreamDone(
            decision_id=decision_id,
            model_used=f"{provider.provider_name}/{model_id}",
            elapsed_ms=elapsed,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            tool_count=tool_count,
            error="" if final_content else "(empty response)",
        )

    # ── 内部 ─────────────────────────────────────────────────────────

    async def _build_messages(
        self, user_input: str, ctx: AgentContext,
        level: AuditLevel, provider_info: str,
        provider: Any = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        # 系统提示词
        if self._system_prompt:
            prompt = self._system_prompt
            if provider_info:
                prompt = (
                    f"## Current Session\n"
                    f"You are answering via the **{provider_info}** model. "
                    f"If asked which model you are, say the model name directly.\n\n"
                    + prompt
                )
            messages.append({"role": "system", "content": prompt})

        # 添加上下文历史
        messages.extend(ctx.messages)
        messages.append({"role": "user", "content": user_input})

        # ── 上下文压缩 ──
        # 估算 token（中英文混合：1 字符 ≈ 0.4 token）
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        estimated_tokens = int(total_chars * 0.4)
        max_tokens = provider.max_context_tokens if provider else 65536
        threshold = int(max_tokens * 0.5)

        if estimated_tokens > threshold and provider and len(ctx.messages) > 6:
            # 保护头（system）+ 尾（最近 4 条），压缩中间
            head = messages[:1]  # system prompt
            tail = messages[-4:]  # last 4 messages (2 turns)
            middle = messages[1:-4]

            if middle:
                try:
                    middle_text = "\n".join(
                        str(m.get("content", ""))[:200] for m in middle
                    )
                    summary = await provider.chat(
                        messages=[{"role": "user",
                                   "content": f"Summarize:\n{middle_text}"}],
                        max_tokens=200,
                    )
                    summary_text = summary.content or ""
                    compressed = head + [{
                        "role": "system",
                        "content": f"[Compressed: {summary_text[:300]}]"
                    }] + tail
                    return compressed
                except Exception:
                    pass  # 压缩失败，返回原始消息

        return messages

    def _get_tools(self) -> list[dict[str, Any]] | None:
        if self._tool_registry:
            return self._tool_registry.get_tools(mode=self._mode)
        return None

    def _get_tool_permission(self, name: str) -> int:
        """获取工具的风险级别。

        默认映射：
          - 内置安全工具 (remember_fact, recall_memory, get_model_status) → SAFE
          - shell_exec → WRITE
          - download_pdf / write_* → WRITE
          - web_search / search_* → SAFE
          - 其他未知工具 → READ（保守）
        """
        # 内置安全工具
        SAFE_TOOLS = {
            "remember_fact", "recall_memory", "get_model_status",
            "web_search", "search_papers",
            "list_dir", "read_file", "browse", "intel_search",
        }
        # 写入工具
        WRITE_TOOLS = {
            "shell_exec", "download_pdf", "write_paper_notes",
            "write_file", "download", "upload", "intel_brief",
        }

        if name in SAFE_TOOLS:
            return 0  # PermissionLevel.SAFE
        if name in WRITE_TOOLS:
            return 2  # PermissionLevel.WRITE
        if name.startswith("write_") or name.startswith("download_"):
            return 2  # PermissionLevel.WRITE
        if name.startswith("search_") or name.startswith("read_") or name.startswith("get_"):
            return 0  # PermissionLevel.SAFE
        return 1  # PermissionLevel.READ（默认保守）

    def confirm_tool(self, allowed: bool) -> None:
        """确认/拒绝待处理的工具调用。

        由 CLI/TUI 在用户做出选择后调用。
        """
        self._confirm_allowed = allowed
        if self._confirm_pending:
            self._confirm_pending.set()

    async def _execute_tool(self, name: str, args: dict) -> str:
        if name == "get_model_status":
            models = self._registry.list_all() if self._registry else []
            return "\n".join(
                f"{p.provider_name}/{p.model_id} tags={p.capabilities}"
                for p in models
            )
        if name == "remember_fact":
            await self._memory.remember(
                key=args.get("key", ""),
                value=args.get("value", ""),
                category=args.get("category", "fact"),
            )
            return f"Memory saved: {args.get('key', '')}"
        if name == "recall_memory":
            results = await self._memory.recall(args.get("query", ""))
            if not results:
                return "No matching memories found."
            return "\n".join(
                f"[{r['category']}] {r['key']}: {r['value'][:100]}"
                for r in results
            )
        return await self._skills.execute(name, args)
