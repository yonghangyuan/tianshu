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
from ..tianyao.agent_scheduler import AgentScheduler as _AgentScheduler
from ..renyao.orchestrator import Orchestrator
from .context_engine import ContextEngine
from ..sdk.trigram import (
    Layer, AgentRef, TrigramMessage, MessageConstraints,
    MessagePriority, MessageDirection,
    AuditSixQuestions, validate_message,
    DecisionContext, DecisionCriterion, decide, FusedEstimate,
    WorldLevel, TIAN_STRATEGY,
)
from .router import ModelRouter, RoutingConfig
from .status import route as _s_route, tool as _s_tool, done as _s_done, error as _s_error


def _classify_error(error: Exception, provider_name: str, model_id: str) -> tuple[str, str]:
    """分类 API 错误，返回 (人类可读提示, 恢复动作)。

    恢复动作: retry | fallback | compress | auth_fix | fatal
    """
    msg = str(error)
    model_ref = f"{provider_name}/{model_id}"

    if any(k in msg.lower() for k in ("connect", "timeout", "resolve", "refused", "network")):
        return (f"🌐 网络不通 ({model_ref})\n   请检查网络或代理设置。", "retry")
    if "401" in msg or "403" in msg or "unauthorized" in msg.lower():
        return (f"🔑 API Key 无效 ({model_ref})\n   请用 /setup 重新配置 Key。", "auth_fix")
    if "400" in msg:
        return (f"⚠️ 请求格式错误 ({model_ref})\n   {msg[:200]}", "fatal")
    if "429" in msg or "rate" in msg.lower():
        return (f"⏳ 请求太频繁 ({model_ref}) — 自动重试中...", "retry")
    if "503" in msg or "overload" in msg.lower() or "unavailable" in msg.lower():
        return (f"🏗️ 模型繁忙 ({model_ref}) — 自动切换...", "fallback")
    if "context" in msg.lower() or "token" in msg.lower() or "maximum" in msg.lower():
        return (f"📏 上下文超长 ({model_ref}) — 自动压缩后重试", "compress")
    detail = msg[:300] if msg.strip() else f"{type(error).__name__}"
    if hasattr(error, 'response') and hasattr(error.response, 'status_code'):
        detail += f"(HTTP {error.response.status_code})"
    return (f"❌ {model_ref}: {detail}", "fatal")


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
        self._agent_scheduler = _AgentScheduler()
        self._orchestrator = Orchestrator(self)
        self._context_engine = ContextEngine()
        self._system_prompt = ""

        # 标记
        self._ready = False
        self._last_reasoning = ""  # 最近一次推理过程（/think 命令查看）
        self._confirm_allowed = False
        self._confirm_pending: Any = None
        self._tool_registry: Any = None  # ToolRegistry
        self._permission_whitelist: set = set()  # "始终允许"的白名单
        self._hooks: dict = {"pre_tool": [], "post_tool": []}  # Hook系统
        self._mode: str = "normal"       # normal / plan / auto
        self._preset: str = "standard"   # standard / minimal / code (PTC) — 与 mode 正交
        self._policy_engine: Any = None  # PolicyEngine
        self._mcp: Any = None            # McpClientManager (lazy init)

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
            ToolInfo("run_code", "Write a Python program that composes multiple tool calls via tools.run(name, args) and returns the final value with submit(value). Intermediate tool results stay inside the program (not sent back to you), so only submit the small final answer.",
                     {"type": "object", "properties": {
                         "code": {"type": "string", "description": "Python source. Available: tools.run(name, args) -> str (sync), submit(value) -> exits with final value."},
                         "timeout": {"type": "integer", "description": "Wall-clock seconds (default 300)", "default": 300},
                     }, "required": ["code"]},
                     None, permission=2, skill_name="core"),
        ]:
            self._tool_registry.register(ti)

        # 加载 Plugins + Cron
        self._plugins.load_all()
        self._cron.load()
        # 加载白名单
        _wpath = Path(db_path).parent / "tool_whitelist.json"
        if _wpath.exists():
            import json as _json
            self._permission_whitelist = set(_json.loads(_wpath.read_text(encoding="utf-8")))

        # ── MCP 客户端 ──
        # 如果之前有旧连接（/reload 场景），先断开
        if self._mcp is not None:
            try:
                import asyncio as _asyncio
                _asyncio.get_event_loop().run_until_complete(
                    self._mcp.disconnect_all()
                )
            except Exception:
                pass

        from .config import load_mcp_config
        mcp_config = load_mcp_config("config/mcp.yaml")
        if mcp_config.get("servers"):
            from ..renyao.mcp_client import McpClientManager
            self._mcp = McpClientManager()
            self._mcp._registry = self._tool_registry
            # 连接在 async 上下文中执行——setup() 是同步的，
            # 实际连接延迟到首次 run() 调用时
            self._mcp_pending_connect = mcp_config.get("servers", {})

        # 自动注入项目上下文 (类似 Claude Code 的 TIANSHU.md)
        _cwd = Path.cwd()
        _project_slug = str(_cwd).replace(":\\", "--").replace("\\", "-").replace("/", "-")
        _project_dir = Path.home() / ".tianshu" / "projects" / _project_slug
        _project_dir.mkdir(parents=True, exist_ok=True)

        _project_context = ""
        # 读: 兼容 CLAUDE.md (复用) + TIANSHU.md (天枢自产)
        for _cand in ["CLAUDE.md", "TIANSHU.md", "README.md", "CONTRIBUTING.md"]:
            _cf = _cwd / _cand
            if _cf.exists():
                _project_context += f"\n\n## 项目上下文 ({_cand})\n{_cf.read_text(encoding='utf-8', errors='replace')[:3000]}\n"
                break

        # 加载项目记忆 (先读天枢的, 再读老 MEMORY.md 兼容)
        for _mem_name in ["TIANSHU_MEMORY.md", "MEMORY.md"]:
            _pmem = _project_dir / _mem_name
            if _pmem.exists():
                _project_context += f"\n\n## 项目记忆 ({_mem_name})\n{_pmem.read_text(encoding='utf-8', errors='replace')[:2000]}\n"
                break

        full_prompt = (_project_context + "\n\n" + system_prompt) if _project_context else system_prompt

        # 存储项目 context 路径以供后续保存
        self._project_dir = _project_dir
        self._project_slug = _project_slug

        self._context_engine.system_prompt = full_prompt
        self._orchestrator.setup(self)

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
    def agent_scheduler(self) -> _AgentScheduler:
        return self._agent_scheduler

    @property
    def orchestrator(self) -> Orchestrator:
        return self._orchestrator

    def add_hook(self, event: str, callback) -> None:
        """注册 Hook: pre_tool / post_tool。回调签名为 (tool_name, args) -> None"""
        if event in self._hooks:
            self._hooks[event].append(callback)

    async def fork_session(self, ctx: AgentContext, name: str = "") -> AgentContext:
        """从当前会话分叉一个新的会话 (#5 Session fork)。"""
        new_ctx = AgentContext(session_id=f"fork_{name}_{int(time.time())}")
        new_ctx.messages = list(ctx.messages[-10:])  # 保留最近10条
        new_ctx.metadata = dict(ctx.metadata)
        return new_ctx

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

        # ── MCP 延迟连接 ──
        if hasattr(self, '_mcp_pending_connect') and self._mcp_pending_connect:
            try:
                count = await self._mcp.connect_all(
                    self._mcp_pending_connect, self._tool_registry
                )
                if count > 0:
                    self._context_engine.system_prompt = (
                        (self._context_engine.system_prompt or "") + self._build_mcp_tools_section()
                    )
            except Exception:
                pass
            finally:
                self._mcp_pending_connect = {}

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
        messages, _ = await self._build_messages(
            user_input, ctx, level, provider_info, provider
        )
        self._inject_preset_instruction(messages)

        # 4. ReAct 循环
        tool_results: list[dict[str, Any]] = []
        reasoning: list[str] = []
        final_content = ""
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cached_tokens = 0

        for _ in range(10):  # MAX_TOOL_ROUNDS
            try:
                _tools = self._get_tools()
                if _tools and "no_tools" in provider.capabilities:
                    _tools = None  # 本地纯聊天模型不带工具（同 run_stream）
                resp = await provider.chat(
                    messages=messages,
                    tools=_tools,
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
            total_cached_tokens += resp.usage.cached_prompt_tokens

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

            # 执行工具 — 安全闸门
            for tc in resp.tool_calls:
                name, args = tc.name, tc.arguments

                # ── 闸门 1: 权限检查 ──
                perm = self._get_tool_permission(name)
                whitelist = getattr(self, '_permission_whitelist', set())
                auto_mode = getattr(self, '_mode', 'normal') == 'auto' or self._preset == 'minimal'

                # ── 闸门 2: 策略引擎 ──
                if self._policy_engine:
                    decision = self._policy_engine.evaluate(name, args)
                    if decision and decision.action == "deny":
                        result_text = f"Policy denied: {decision.message} [{decision.policy_name}]"
                        tool_results.append({"name": name, "success": False, "output": result_text[:500]})
                        _s_tool(name, f"DENIED by {decision.policy_name}", 0)
                        continue

                # ── 闸门 3: 写入确认 ──
                if perm >= 2 and name not in whitelist and not auto_mode:
                    result_text = (
                        f"Tool '{name}' requires confirmation (permission={perm}). "
                        "Use 'always allow' in interactive CLI or switch to auto mode."
                    )
                    tool_results.append({"name": name, "success": False, "output": result_text[:500]})
                    _s_tool(name, "CONFIRM_REQUIRED", 0)
                    continue

                t0_tool = time.time()
                try:
                    output = await self._execute_tool(name, args, ctx)
                    success = True
                    result_text = str(output)
                except Exception as e:
                    output = str(e)
                    success = False
                    result_text = f"Error: {e}"
                _s_tool(name, result_text[:60], int((time.time()-t0_tool)*1000))

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
            cached_tokens=total_cached_tokens,
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

        # ── MCP 延迟连接 ──
        if hasattr(self, '_mcp_pending_connect') and self._mcp_pending_connect:
            try:
                count = await self._mcp.connect_all(
                    self._mcp_pending_connect, self._tool_registry
                )
                # 动态注入 MCP 工具列表到 system prompt
                if count > 0:
                    mcp_tools_section = self._build_mcp_tools_section()
                    self._context_engine.system_prompt = (
                        (self._context_engine.system_prompt or "") + mcp_tools_section
                    )
            except Exception as e:
                yield ContentDelta(
                    text=f"\n⚠️ MCP 连接失败: {type(e).__name__}: {e}\n"
                )
            finally:
                self._mcp_pending_connect = {}

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
        messages, comp_meta = await self._build_messages(
            request.input, ctx, level, provider_info, provider
        )
        self._inject_preset_instruction(messages)

        # 注入上一轮工具摘要
        if hasattr(ctx, 'turns') and ctx.turns:
            last = ctx.turns[-1]
            if last.tool_results:
                tool_names = [t.get('name', '?') for t in last.tool_results]
                messages.append({
                    "role": "system",
                    "content": f"[上轮工具调用: {', '.join(tool_names)}]",
                })

        # 通知用户上下文压缩
        if comp_meta:
            tail_info = {1: "保留最近3轮", 2: "保留最近2轮", 3: "保留最近1轮"}
            yield ContentDelta(
                text=(
                    f"\n💾 上下文已压缩 (L{comp_meta['level']}: "
                    f"{comp_meta['before_chars']}→{comp_meta['after_chars']}字符, "
                    f"{tail_info.get(comp_meta['level'], '')}, "
                    f"审计ID: {comp_meta['stored_decision_id'][:8]})\n"
                )
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
        total_cached_tokens = 0
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
                # 最后一轮不传 tools，强制纯文本回复；
                # no_tools 标签模型（本地纯聊天兜底）全程不带工具——
                # 工具 schema 会把弱模型带偏（对着工具列表自说自话）
                tools_for_round = None if _budget_exhausted else self._get_tools()
                if tools_for_round and "no_tools" in provider.capabilities:
                    tools_for_round = None
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
                        total_cached_tokens += chunk.usage.cached_prompt_tokens

            except Exception as e:
                error_msg, recovery = _classify_error(e, provider.provider_name, model_id)
                # 自动恢复: retry/fallback 时尝试替代模型
                if recovery == "retry":
                    await asyncio.sleep(2)
                    # 重试同模型一次
                elif recovery == "fallback" and self._registry:
                    fallback_list = self._registry.list_all()
                    if len(fallback_list) > 1:
                        # 换一个模型重试
                        for fb in fallback_list:
                            if fb.model_id != model_id:
                                provider = fb
                                model_id = fb.model_id
                                yield ContentDelta(text=f"\n[dim]已切换至 {fb.provider_name}/{fb.model_id}[/dim]\n")
                                break
                elif recovery == "compress":
                    yield ContentDelta(text=f"\n[dim]上下文已自动压缩[/dim]\n")
                if recovery != "fatal":
                    continue  # 重试当前轮
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
                # automode / minimal 预设: 跳过所有确认
                if getattr(self, '_automode', False) or self._preset == 'minimal':
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
                    elif decision.action == "confirm" and not (
                        getattr(self, '_automode', False) or self._preset == 'minimal'
                    ):
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

                # ── 天枢三层闸门: 决策引擎风险评估 ──
                # 仅高风险工具触发。低风险直接跳过，零开销。
                tool_info = self._tool_registry.get(name) if self._tool_registry else None
                stakes = tool_info.stakes if tool_info else None
                risk_blocked = False

                if self._preset != 'minimal' and stakes is not None and (
                    stakes.reversibility > 0.6 or stakes.max_loss > 0.5
                ):
                    risk_perm = float(tool_info.permission) if tool_info else float(perm)
                    risk_posterior = FusedEstimate(
                        posterior_mean=risk_perm,
                        posterior_variance=1.0 - min(stakes.model_confidence, 0.99),
                        confidence_95=(risk_perm - 1.0, risk_perm + 1.0),
                        source_count=1,
                    )

                    def _exec_loss(theta: float) -> float:
                        return stakes.max_loss * (theta / 3.0)

                    def _skip_loss(theta: float) -> float:
                        return 0.1

                    risk_decision = decide(
                        risk_posterior,
                        [("execute", _exec_loss), ("skip", _skip_loss)],
                        stakes,
                    )

                    if risk_decision.chosen_action in ("skip", "no_action"):
                        risk_blocked = True
                        result_text = (
                            f"⛔ 天层风险评估否决 [{risk_decision.criterion.value}]: "
                            f"{risk_decision.rationale}"
                        )
                        yield ToolCallResult(
                            tool_name=name,
                            success=False,
                            output=result_text,
                            elapsed_ms=0,
                        )
                        tool_results.append({
                            "name": name,
                            "success": False,
                            "output": result_text,
                        })
                        # 反馈否决给 LLM
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
                        continue

                # ── 执行工具 ──
                t0_tool = time.time()
                diff_preview = _try_diff(name, args)  # write_file 时生成 diff

                try:
                    output = await self._execute_tool(name, args, ctx)
                    success = True
                    result_text = str(output)
                except FileNotFoundError as e:
                    success = False
                    result_text = f"📁 文件未找到: {e}"
                except PermissionError as e:
                    success = False
                    result_text = f"🔒 权限不足: {e}"
                except TimeoutError as e:
                    success = False
                    result_text = f"⏱️ 工具超时: {e}"
                except Exception as e:
                    success = False
                    result_text = f"❌ 工具执行失败 ({name}): {type(e).__name__}: {e}"

                elapsed_tool = int((time.time() - t0_tool) * 1000)
                _s_tool(name, result_text[:60], elapsed_tool)

                # 附 diff 到结果中
                result_display = result_text[:500]
                if diff_preview and success:
                    result_display = f"{result_text[:200]}\n\n--- Diff ---\n{diff_preview[:500]}"

                yield ToolCallResult(
                    tool_name=name,
                    success=success,
                    output=result_display,
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
            cached_tokens=total_cached_tokens,
            tool_count=tool_count,
            error="" if final_content else "(empty response)",
        )

    # ── 内部 ─────────────────────────────────────────────────────────

    async def _build_messages(
        self, user_input: str, ctx: AgentContext,
        level: AuditLevel, provider_info: str,
        provider: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """构建消息 → 委托给 ContextEngine。"""
        return await self._context_engine.assemble(
            user_input,
            ctx.messages,
            provider,
            self._audit,
            provider_info=provider_info,
            memory_provider=self._memory.provider if hasattr(self._memory, 'provider') else None,
        )

    def _get_tools(self) -> list[dict[str, Any]] | None:
        if self._tool_registry:
            return self._tool_registry.get_tools(mode=self._mode, preset=self._preset)
        return None

    def _inject_preset_instruction(self, messages: list[dict]) -> None:
        """把当前预设的说明注入 messages 头部（每次 run/run_stream 调用一次）。

        standard 预设无说明，零开销。合并进首条 system 消息以避免重复 role。
        """
        from .presets import get_preset
        p = get_preset(self._preset)
        if not p.instruction:
            return
        if messages and messages[0].get("role") == "system":
            first = dict(messages[0])
            first["content"] = f"{first.get('content', '')}\n\n{p.instruction}"
            messages[0] = first
        else:
            messages.insert(0, {"role": "system", "content": p.instruction})

    def _build_mcp_tools_section(self) -> str:
        """构建 MCP 工具参考——动态注入 system prompt。"""
        if not self._mcp:
            return ""
        tools = self._mcp.list_tools()
        if not tools:
            return ""

        lines = ["", "## 已连接的 MCP 工具", ""]
        by_server: dict[str, list] = {}
        for t in tools:
            srv = t.get("server", "other")
            by_server.setdefault(srv, []).append(f"`{t['name']}` — {t.get('description', '')[:100]}")

        for srv, tls in sorted(by_server.items()):
            lines.append(f"### {srv}")
            for tl in tls:
                lines.append(f"- {tl}")
        lines.append("")
        return "\n".join(lines)

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
            "edit_file", "run_code",
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

    def confirm_tool(self, allowed: bool, *, always: bool = False) -> None:
        """确认/拒绝待处理的工具调用。

        由 CLI/TUI 在用户做出选择后调用。
        always=True → 加入白名单并持久化 (pip install 目录下的 tool_whitelist.json)
        """
        self._confirm_allowed = allowed
        if always and allowed and self._confirm_pending:
            # 记住"始终允许"
            # 从 pending 上下文中获取工具名（由 main.py 设置）
            pass  # 在 main.py 的 _handle_confirm 中处理
        if self._confirm_pending:
            self._confirm_pending.set()

    async def _execute_tool(self, name: str, args: dict, ctx: "AgentContext | None" = None) -> str:
        if name == "shell_exec" and self._preset == "minimal" and ctx is not None:
            # minimal 预设：走持久 shell（cwd/env 跨调用保持）
            from tianshu.diyao.persistent_shell import PersistentShell
            if ctx.shell is None:
                ctx.shell = PersistentShell()
            return await ctx.shell.run(
                args.get("command", ""),
                timeout=float(args.get("timeout", 60)),
            )
        if name == "run_code":
            # PTC 代码模式：模型写 Python 程序组合工具调用
            from .ptc import run_program
            code = args.get("code", "")
            if not code:
                return "[run_code] code 参数为空"
            return await run_program(
                code,
                self._ptc_exec,
                timeout=float(args.get("timeout", 300)),
                cwd=args.get("cwd", ""),
            )
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

    async def _ptc_exec(self, name: str, args: dict) -> str:
        """PTC 程序内工具调用：策略 deny→错误；confirm→拒绝（子进程内无法交互）；其余正常执行。"""
        if self._policy_engine and self._policy_engine.enabled:
            decision = self._policy_engine.evaluate(name, args)
            if decision and decision.action == "deny":
                return f"[策略拒绝] {decision.message or decision.policy_name}"
            if decision and decision.action == "confirm":
                return "[策略确认无法在代码模式内交互，已拒绝]"
        try:
            return str(await self._skills.execute(name, args))
        except Exception as e:
            return f"[工具失败] {type(e).__name__}: {e}"

    # ── 三爻通道 demo ─────────────────────────────────────────────

    async def execute_via_trigram(
        self, tool_name: str, args: dict, user_intent: str = ""
    ) -> dict:
        """[Demo] 通过天·人·地三层通道执行一次工具调用。

        这是三爻接口规范的运行时 demo，不替代现有的 _execute_tool()。
        只演示一条工具调用如何经过三层闸门。

        流程:
          地(search skill) → 人(AgentCore调度) → 天(PolicyEngine审查)
              ↓                                       │
              │              ← OVERRIDE 否决 ← ───────┘ (违规时)
              ↓
           执行 → 审计六问记录

        Returns:
            {
                "allowed": bool,
                "result": str,
                "message_chain": [TrigramMessage, ...],
                "audit": dict,
            }
        """
        chain: list[TrigramMessage] = []

        # Agent 标识
        di_ref = AgentRef(Layer.DI, tool_name)
        ren_ref = AgentRef(Layer.REN, "agent_core")
        tian_ref = AgentRef(Layer.TIAN, "policy_engine")

        intent = user_intent or f"执行工具: {tool_name}({_brief_args(args)})"

        # ── 第一道闸门: 地→人 (工具请求执行) ──
        msg_di_to_ren = TrigramMessage.create(
            source=di_ref, target=ren_ref,
            intent=intent,
            payload={"tool_name": tool_name, "args": args},
        )
        errors = validate_message(msg_di_to_ren)
        if errors:
            return {
                "allowed": False,
                "result": f"消息校验失败: {'; '.join(errors)}",
                "message_chain": chain,
                "audit": None,
            }
        chain.append(msg_di_to_ren)

        # ── 第二道闸门: 人→天 (请求权限审查) ──
        perm = self._get_tool_permission(tool_name)
        msg_ren_to_tian = TrigramMessage.create(
            source=ren_ref, target=tian_ref,
            intent=f"请求权限审查: {tool_name} (PermissionLevel={perm})",
            payload={"tool_name": tool_name, "args": args, "permission_level": perm},
            constraints=MessageConstraints(permission_level=perm),
            priority=MessagePriority.URGENT if perm >= 2 else MessagePriority.NORMAL,
        )
        chain.append(msg_ren_to_tian)

        # ── 第三道闸门: 天层审查 (PolicyEngine) ──
        if self._policy_engine and self._policy_engine.enabled:
            decision = self._policy_engine.evaluate(tool_name, args)
            if decision.action == "deny":
                override = TrigramMessage.create(
                    source=tian_ref, target=ren_ref,
                    intent=f"否决: {decision.message or decision.policy_name}",
                    priority=MessagePriority.OVERRIDE,
                )
                chain.append(override)
                audit = AuditSixQuestions.record(
                    agent=tian_ref,
                    info=[f"tool={tool_name}", f"args={_brief_args(args)}"],
                    alternatives=["execute", "deny"],
                    rules=[decision.policy_name],
                    decision=f"deny: {decision.message or decision.policy_name}",
                )
                return {
                    "allowed": False,
                    "result": f"天层否决: {decision.message or decision.policy_name}",
                    "message_chain": [m.to_dict() for m in chain],
                    "audit": audit.to_dict(),
                }

        # ── 第三道闸门 (续): 决策引擎风险评估 ──
        # 策略引擎放行后，决策引擎根据场景利害做二次判断。
        # 同一个工具，低风险场景直接执行，关键安全场景可能因预防原则被否决。
        tool_info = self._tool_registry.get(tool_name) if self._tool_registry else None
        stakes = tool_info.stakes if tool_info else None

        # 世界层级感知: 天层根据 Agent 所处的世界层级切换策略
        world_level = (
            tool_info.world_level if tool_info and hasattr(tool_info, 'world_level')
            else WorldLevel.MEASURABLE
        )
        tian_strategy = TIAN_STRATEGY.get(world_level, TIAN_STRATEGY[WorldLevel.MEASURABLE])

        # UNOBSERVABLE → 天层沉默，不做任何风险评估
        if world_level == WorldLevel.UNOBSERVABLE:
            risk_blocked = False
            risk_passed = False  # 不阻止，但标记"无判断能力"

        elif stakes is not None and (
            stakes.reversibility > 0.6 or stakes.max_loss > 0.5
        ):
            # 需要风险评估——构建简化后验
            risk_perm = float(tool_info.permission) if tool_info else float(perm)
            risk_posterior = FusedEstimate(
                posterior_mean=risk_perm,  # 权限级别作为风险指标
                posterior_variance=1.0 - min(stakes.model_confidence, 0.99),
                confidence_95=(float(perm) - 1.0, float(perm) + 1.0),
                source_count=1,
            )

            def execute_loss(theta: float) -> float:
                return stakes.max_loss * (theta / 3.0)  # 权限越高损失期望越大

            def skip_loss(theta: float) -> float:
                return 0.1  # 跳过的固定机会成本

            risk_decision = decide(
                risk_posterior,
                [("execute", execute_loss), ("skip", skip_loss)],
                stakes,
            )

            if risk_decision.chosen_action in ("skip", "no_action"):
                override = TrigramMessage.create(
                    source=tian_ref, target=ren_ref,
                    intent=(
                        f"风险评估否决: {risk_decision.rationale}"
                    ),
                    priority=MessagePriority.OVERRIDE,
                )
                chain.append(override)
                audit = AuditSixQuestions.record(
                    agent=tian_ref,
                    info=[f"tool={tool_name}",
                          f"stakes=reversibility={stakes.reversibility},max_loss={stakes.max_loss}"],
                    alternatives=["execute", "skip"],
                    rules=[risk_decision.criterion.value],
                    decision=f"risk_deny: {risk_decision.rationale}",
                )
                return {
                    "allowed": False,
                    "result": f"天层风险评估否决 [{risk_decision.criterion.value}]: {risk_decision.rationale}",
                    "message_chain": [m.to_dict() for m in chain],
                    "audit": audit.to_dict(),
                }

            # 通过 → 记录风险评估结果到审计
            risk_passed = True
        else:
            risk_passed = False  # 低风险，无需评估

        # ── 执行 ──
        try:
            result = await self._execute_tool(tool_name, args)
            success = True
            # 反馈闭环: 交付结果后更新传感器可靠性
            if self._tool_registry and self._memory:
                ti = self._tool_registry.get(tool_name)
                if ti and getattr(ti, 'call_count', 0) > 0:
                    try:
                        from ..sdk.trigram import EntityDynamics
                        _sensor_chars = getattr(ti, '_sensor_chars', None)
                        if _sensor_chars:
                            from ..sdk.trigram import update_sensor_reliability
                            update_sensor_reliability(
                                _sensor_chars, float(ti.call_count), time.time(),
                                float(ti.call_count + ti.error_count),  # ground truth: total attempts
                                EntityDynamics.from_preset("static"),
                            )
                    except Exception:
                        pass
                if ti:
                    ti.call_count += 1
        except Exception as e:
            result = str(e)
            success = False
            # 记录失败到 error_count
            if self._tool_registry:
                ti = self._tool_registry.get(tool_name)
                if ti:
                    ti.error_count += 1

        # ── 结果回报: 地→人 ──
        msg_result = TrigramMessage.create(
            source=di_ref, target=ren_ref,
            intent=f"{'完成' if success else '失败'}: {tool_name}",
            payload={"tool_name": tool_name, "success": success,
                     "result": str(result)[:500]},
        )
        chain.append(msg_result)

        # ── 审计六问 ──
        rules_used = (
            [r.get("name", "") for r in self._policy_engine.list_rules()]
            if self._policy_engine else []
        )
        if risk_passed:
            rules_used.append("risk_assessment:passed")
        audit = AuditSixQuestions.record(
            agent=di_ref,
            info=[f"tool={tool_name}", f"args={_brief_args(args)}"],
            alternatives=["execute", "skip"],
            rules=rules_used,
            decision=f"{'execute' if success else 'fail'}: {tool_name}",
            outcome=str(result)[:200],
        )

        return {
            "allowed": True,
            "result": str(result),
            "message_chain": [m.to_dict() for m in chain],
            "audit": audit.to_dict(),
        }


def _brief_args(args: dict) -> str:
    """参数摘要——防止 args 过长撑爆审计记录。"""
    items = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        items.append(f"{k}={s}")
    return ", ".join(items) if items else "(none)"


def _compute_diff(old: str, new: str, filepath: str = "", context_lines: int = 3) -> str:
    """生成 unified diff——写文件前预览变更。实现委托给 diyao.diff（file_ops 共用）。"""
    from tianshu.diyao.diff import compute_diff
    return compute_diff(old, new, filepath=filepath, context_lines=context_lines)


def _try_diff(tool_name: str, args: dict) -> str:
    """对写文件/行级编辑操作生成 diff 预览。"""
    path = args.get("path") or args.get("file_path") or args.get("filename") or ""
    if not path:
        return ""
    try:
        import os as _os
        if tool_name == "write_file":
            content = args.get("content") or ""
            if not content:
                return ""
            if _os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
                if old_content != content:
                    return _compute_diff(old_content, content, path)
            else:
                return f"[新文件] {path}\n+ {len(content)} chars"
        elif tool_name == "edit_file":
            old_string = args.get("old_string") or ""
            new_string = args.get("new_string") or ""
            if not old_string or not _os.path.exists(path):
                return ""
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                old_content = f.read()
            if old_string not in old_content:
                return ""
            replace_all = bool(args.get("replace_all"))
            new_content = old_content.replace(old_string, new_string) if replace_all \
                else old_content.replace(old_string, new_string, 1)
            if old_content != new_content:
                return _compute_diff(old_content, new_content, path)
    except Exception:
        return ""
    return ""

