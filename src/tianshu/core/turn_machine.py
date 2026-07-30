"""Conversation Turn State Machine — 结构化 Agent Loop。

将 run_stream() 拆分为清晰的阶段，每个阶段有扩展点。
不改行为，只改组织——为未来异常处理留空间。

Hermes conversation_loop.py 7034 行的教训：
  Happy path ≈ 500 行。剩下 6500 行是异常处理。
  如果结构不先拆好，异常处理无处可装。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class TurnConfig:
    """回合配置。"""
    token_budget: int = 500000     # DS v4-pro 支持 1M，留 50% 给 context
    budget_warn_ratio: float = 0.6  # 60% 提醒
    budget_stop_ratio: float = 0.85 # 85% 强制停止
    max_rounds: int = 24
    confirm_timeout: int = 60
    dup_window: int = 3


class TurnContext:
    """回合运行时上下文——在各个阶段之间流转。"""

    def __init__(self, core, request, ctx, config: TurnConfig):
        self.core = core
        self.request = request
        self.ctx = ctx
        self.config = config

        # 路由结果
        self.provider: Any = None
        self.model_id: str = ""
        self.audit_level: Any = None

        # 消息
        self.messages: list[dict] = []
        self.user_input: str = request.input

        # Token 追踪
        self.tokens_prompt: int = 0
        self.tokens_completion: int = 0
        self.budget_warned: bool = False
        self.budget_exhausted: bool = False

        # 工具追踪
        self.tool_count: int = 0
        self.round: int = 0
        self.last_sigs: list[str] = []

        # 内容收集
        self.reasoning_chain: list[str] = []
        self.final_content: str = ""
        self.tool_results: list[dict] = []

        # 时间
        self.t0: float = 0.0
        self.decision_id: str = ""


# ═══════════════════════════════════════════════════════════════════
# 阶段实现（纯逻辑，不 yield）
# ═══════════════════════════════════════════════════════════════════

class TurnStages:
    """各阶段的实现。每个方法返回阶段结果，由 run_stream() 负责 yield 事件。"""

    # ── Phase 1: SETUP ──────────────────────────────────────

    @staticmethod
    def setup(tc: TurnContext) -> str | None:
        """验证就绪。返回 None=成功，返回错误消息。"""
        if not tc.core._ready:
            return "AgentCore not set up. Call setup() first."
        tc.t0 = time.time()
        tc.decision_id = tc.core._audit.generate_id()
        return None

    # ── Phase 2: ROUTE ──────────────────────────────────────

    @staticmethod
    async def route(tc: TurnContext) -> str | None:
        """选择 Provider/Model。返回 None=成功。"""
        core, req, ctx = tc.core, tc.request, tc.ctx
        override = req.model_override or ctx.metadata.get("model_override", "")
        if override:
            pn, _, ms = override.partition("/")
            provider, model_id, level = core.router.route_direct(pn, ms or pn)
        else:
            provider, model_id, level = await core.router.route(req.task_type)

        if provider is None:
            return f"No model available for task_type={req.task_type}"

        tc.provider = provider
        tc.model_id = model_id
        tc.audit_level = level
        return None

    # ── Phase 3: BUILD ──────────────────────────────────────

    @staticmethod
    async def build(tc: TurnContext) -> list[dict]:
        """构建消息列表。处理"继续"检测。"""
        core, req, ctx = tc.core, tc.request, tc.ctx
        provider_info = f"{tc.provider.provider_name}/{tc.model_id}"

        user_input = req.input
        # "继续" 检测
        if user_input.strip() in ("继续", "continue", "接着", "go on"):
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

        tc.user_input = user_input
        return await core._build_messages(
            user_input, ctx, tc.audit_level, provider_info, tc.provider,
        )

    # ── Phase 4: BUDGET CHECK ───────────────────────────────

    @staticmethod
    def check_budget(tc: TurnContext) -> str | None:
        """检查 token 预算。返回 None=继续，返回消息体=注入提醒。"""
        used = tc.tokens_prompt + tc.tokens_completion
        cfg = tc.config

        if used > cfg.token_budget * cfg.budget_stop_ratio:
            tc.budget_exhausted = True
            return f"Token 预算耗尽 ({used // 1000}K)。禁止再调工具，直接给出最终回答。"

        if used > cfg.token_budget * cfg.budget_warn_ratio and not tc.budget_warned:
            tc.budget_warned = True
            return f"已消耗 {used // 1000}K tokens。请基于现有信息尽快回答，只在必要时再搜一次。"

        return None

    # ── Phase 5: TOOL EXECUTION ──────────────────────────────

    @staticmethod
    def check_duplicate(tc: TurnContext, name: str, args: dict) -> bool:
        """检查重复调用。返回 True=应跳过。"""
        cfg = tc.config
        sig = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        tc.last_sigs.append(sig)
        if len(tc.last_sigs) > cfg.dup_window:
            tc.last_sigs.pop(0)
        return tc.last_sigs.count(sig) >= 2

    @staticmethod
    def permission_needed(tc: TurnContext, name: str) -> bool:
        """是否需要权限确认。"""
        if getattr(tc.core, '_automode', False):
            return False
        perm = tc.core._get_tool_permission(name)
        whitelist = getattr(tc.core, '_permission_whitelist', set())
        return perm >= 2 and name not in whitelist

    @staticmethod
    async def execute_tool(tc: TurnContext, name: str, args: dict) -> tuple[bool, str]:
        """执行工具。返回 (success, output)。"""
        try:
            output = await tc.core._execute_tool(name, args)
            return True, str(output)
        except Exception as e:
            return False, str(e)

    @staticmethod
    def build_feedback(
        tc: TurnContext, round_content: str, round_reasoning: str,
        tool_name: str, tool_args: dict, tool_id: str, result_text: str,
    ) -> list[dict]:
        """构建工具反馈消息，返回要 append 到 messages 的消息列表。"""
        asst_msg: dict[str, Any] = {
            "role": "assistant", "content": round_content or "",
            "tool_calls": [{
                "id": tool_id, "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(tool_args, ensure_ascii=False)
                    if isinstance(tool_args, dict) else str(tool_args),
                },
            }],
        }
        if round_reasoning:
            asst_msg["reasoning_content"] = round_reasoning
        return [
            asst_msg,
            {"role": "tool", "tool_call_id": tool_id, "content": result_text[:2000]},
        ]

    # ── Phase 6: FINALIZE ───────────────────────────────────

    @staticmethod
    def finalize_context(tc: TurnContext):
        """更新 ctx.messages。"""
        ctx, req = tc.ctx, tc.request
        core = tc.core

        ctx.messages.append({"role": "user", "content": req.input})
        offset = (1 if core._system_prompt else 0) + len(ctx.messages) - 1
        for m in tc.messages[offset:]:
            ctx.messages.append(m)

        if tc.final_content:
            ctx.messages.append({"role": "assistant", "content": tc.final_content})
        elif tc.tool_count > 0:
            ctx.messages.append({
                "role": "assistant",
                "content": (
                    f"[已通过 {tc.tool_count} 次工具调用收集信息。"
                    f"输入'继续'让我基于已有结果给出答案。]"
                ),
            })

    # ── Phase 7: POST-TURN HOOKS ─────────────────────────────

    @staticmethod
    async def run_post_turn(tc: TurnContext):
        """回合后处理：审计+记忆+自进化。"""
        core, ctx, req = tc.core, tc.ctx, tc.request

        from ..sdk.models import AuditRecord
        record = AuditRecord(
            decision_id=tc.decision_id, timestamp=time.time(),
            llm_model=f"{tc.provider.provider_name}/{tc.model_id}",
            reasoning_chain=tc.reasoning_chain, input_data=[],
            level=int(tc.audit_level), session_id=ctx.session_id,
            task_type=req.task_type,
        )
        asyncio.ensure_future(core._audit.record(record))

        core._skills.observe_turn(req.input)
        core._evolution_counter = getattr(core, '_evolution_counter', 0) + 1
        if core._evolution_counter % 5 == 0:
            await core._skills.evolve_skills(tc.provider)

        if tc.final_content:
            try:
                await core._memory.auto_profile(req.input, tc.final_content, tc.provider)
            except Exception:
                pass
            await core._memory.remember(
                key=f"conv_{tc.decision_id}",
                value=f"Q: {req.input[:100]}\nA: {tc.final_content[:200]}",
                category="conversation", session_id=ctx.session_id,
            )

    @staticmethod
    def parse_raw_tool_calls(raw: list[dict]) -> list[dict]:
        """解析原始 tool_call deltas。"""
        result = []
        for tc in raw:
            name = tc.get("name", "")
            tc_id = tc.get("id", "")
            args_str = tc.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except Exception:
                args = {}
            result.append({"id": tc_id, "name": name, "arguments": args})
        return result
