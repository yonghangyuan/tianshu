"""Planner —— 结构化任务规划。

Plan Mode 不只是过滤工具。真正的工作流：
  1. LLM 理解用户意图 → 生成步骤列表
  2. 逐步执行，跟踪进度
  3. 步骤失败 → 调整计划
  4. 全部完成 → 总结

与 ReAct 循环的区别：
  ReAct: 想一步做一步（临场反应）
  Plan:  先想清楚再做（结构化执行）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    """计划中的一步。"""
    id: int
    goal: str             # 这步要达成什么
    tool_hint: str = ""   # 建议用什么工具
    status: str = "pending"  # pending | running | done | failed | skipped
    result_summary: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def elapsed(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return 0.0


@dataclass
class Plan:
    """一份完整的执行计划。"""
    goal: str = ""                 # 用户原始意图
    steps: list[PlanStep] = field(default_factory=list)
    current_step: int = 0
    created_at: float = 0.0

    @property
    def progress(self) -> str:
        done = sum(1 for s in self.steps if s.status == "done")
        failed = sum(1 for s in self.steps if s.status == "failed")
        total = len(self.steps)
        return f"{done}/{total} done" + (f" ({failed} failed)" if failed else "")

    def current(self) -> PlanStep | None:
        if 0 <= self.current_step < len(self.steps):
            return self.steps[self.current_step]
        return None

    def advance(self) -> PlanStep | None:
        self.current_step += 1
        return self.current()


# ── 计划生成提示词 ─────────────────────────────────────────────

PLAN_PROMPT = """你是一个任务规划器。用户给你一个目标，你输出一个 JSON 格式的执行计划。

规则：
1. 每个步骤必须具体、可执行——不是"搜索"，是"用 sogou_weixin 搜索关键词 A"
2. 步骤数量: 简单任务 2-3 步，复杂任务 5-8 步，不超过 10 步
3. 每步建议一个工具（tool_hint），但执行时可以不限于它
4. 考虑失败情况——如果某步可能失败，后面有备选方案
5. 最后一步必须是"总结"——整合前面所有步骤的结果

输出格式（纯 JSON，不要 markdown 代码块）:
{
  "goal": "用户目标的简洁重述",
  "steps": [
    {"goal": "第一步的具体目标", "tool_hint": "建议工具名"},
    {"goal": "第二步的具体目标", "tool_hint": "建议工具名"}
  ]
}

现在，为以下目标生成计划："""


def build_planner_prompt(user_goal: str) -> str:
    return PLAN_PROMPT + "\n" + user_goal


def parse_plan_from_json(text: str) -> Plan | None:
    """从 LLM 输出中提取 JSON 计划。"""
    # 清理可能的 markdown 代码块
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if len(lines) > 1 else text
        if text.endswith("```"):
            text = text[:-3]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取 JSON 片段
        import re
        match = re.search(r'\{[\s\S]*"goal"[\s\S]*"steps"[\s\S]*\}', text)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return None
        else:
            return None

    plan = Plan(
        goal=data.get("goal", ""),
        created_at=time.time(),
    )
    for i, step_data in enumerate(data.get("steps", [])):
        plan.steps.append(PlanStep(
            id=i + 1,
            goal=step_data.get("goal", f"Step {i + 1}"),
            tool_hint=step_data.get("tool_hint", ""),
        ))
    return plan if plan.steps else None


def format_plan_for_display(plan: Plan) -> str:
    """人类可读的计划展示。"""
    lines = [f"📋 计划: {plan.goal}\n"]
    for step in plan.steps:
        icon = {"pending": "⬜", "running": "🔄", "done": "✅",
                "failed": "❌", "skipped": "⏭️"}.get(step.status, "⬜")
        hint = f"  [{step.tool_hint}]" if step.tool_hint else ""
        result = f" → {step.result_summary[:80]}" if step.result_summary else ""
        lines.append(f"  {icon} Step {step.id}: {step.goal}{hint}{result}")
    lines.append(f"\n  进度: {plan.progress}")
    return "\n".join(lines)


def format_plan_ascii(plan: Plan) -> str:
    """ASCII 安全版本——终端兼容。"""
    lines = [f"Plan: {plan.goal}"]
    for step in plan.steps:
        icon = {"pending": "[ ]", "running": "[>]", "done": "[x]",
                "failed": "[!]", "skipped": "[-]"}.get(step.status, "[ ]")
        lines.append(f"  {icon} {step.id}. {step.goal}")
    lines.append(f"  Progress: {plan.progress}")
    return "\n".join(lines)
