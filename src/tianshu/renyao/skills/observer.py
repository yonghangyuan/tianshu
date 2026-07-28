"""自进化观测器 — 观测工具调用模式，自动生成 Skills。

借鉴 Hermes 自进化机制：
  1. 记录每次 Agent 会话的工具调用序列
  2. 序列存入 ~/.tianshu/observations.jsonl
  3. 同类序列 ≥5 次 → 触发自动生成 SKILL.md
  4. 已存在的 Skill 每次使用 usage_count+1
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolSequence:
    """一次完整的工具调用序列。"""
    tools: list[str]              # 按顺序的工具名
    task_description: str         # 用户原始输入
    success: bool
    timestamp: float = field(default_factory=time.time)


def _obs_file() -> Path:
    return Path.home() / ".tianshu" / "observations.jsonl"


def _skills_dir() -> Path:
    d = Path.home() / ".tianshu" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


class SkillObserver:
    """观测工具调用模式，触发 Skill 自进化。

    用法:
        obs = SkillObserver()
        obs.add_call("search_papers", True)      # 每次工具调用
        obs.finish_turn("搜索RL论文")             # 每轮对话结束
        new_skills = obs.check_evolution()       # 检查是否需要生成
    """

    EVOLVE_THRESHOLD = 5  # 同类序列 ≥5 次触发进化

    def __init__(self) -> None:
        self._current_calls: list[str] = []
        self._call_success: list[bool] = []
        self._current_task: str = ""

    # ── 记录 ─────────────────────────────────────────────────────────

    def add_call(self, tool_name: str, success: bool) -> None:
        """记录一次工具调用。"""
        self._current_calls.append(tool_name)
        self._call_success.append(success)

    def finish_turn(self, task_description: str) -> ToolSequence | None:
        """一轮对话结束，保存工具调用序列。"""
        if not self._current_calls:
            return None

        seq = ToolSequence(
            tools=list(self._current_calls),
            task_description=task_description,
            success=all(self._call_success),
        )
        self._persist(seq)
        self._current_calls = []
        self._call_success = []
        self._current_task = ""
        return seq

    def _persist(self, seq: ToolSequence) -> None:
        """写入 observations.jsonl。"""
        f = _obs_file()
        try:
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "tools": seq.tools,
                    "task": seq.task_description[:200],
                    "success": seq.success,
                    "timestamp": seq.timestamp,
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 进化检测 ─────────────────────────────────────────────────────

    def build_skill_prompt(self, pattern: dict[str, Any]) -> str:
        """生成 SKILL.md 的 LLM 提示词。"""
        tools = pattern.get("tools", [])
        examples = pattern.get("example_tasks", [])
        name = pattern.get("name", "auto-skill")
        return f"""Generate a SKILL.md file in YAML frontmatter + Markdown format for this AI agent skill.

Skill name: {name}
Tools used (in order): {' → '.join(tools)}
Example tasks from user:
{chr(10).join(f'- {e}' for e in examples[:3])}

Output format:
---
name: {name}
description: <one-line description>
trigram: <地|人|天|地+人>
tools:
{chr(10).join(f'  - {t}' for t in tools)}
trigger_keywords:
  - <keyword1>
  - <keyword2>
version: 1
usage_count: 0
created: auto
---

# {name}

## When to use
<when to trigger this skill>

## Workflow
{chr(10).join(f'{i}. {t}()' for i, t in enumerate(tools, 1))}

## Output
<what this skill produces>
"""

    def mark_processed(self, signature: str) -> None:
        """标记模式已处理，避免重复生成。"""
        f = _obs_file()
        processed_file = f.parent / ".processed_patterns"
        try:
            existing = set()
            if processed_file.exists():
                existing = set(processed_file.read_text().split("\n"))
            existing.add(signature)
            processed_file.write_text("\n".join(existing))
        except Exception:
            pass

    def is_processed(self, signature: str) -> bool:
        """检查模式是否已处理过。"""
        processed_file = _obs_file().parent / ".processed_patterns"
        if not processed_file.exists():
            return False
        return signature in processed_file.read_text().split("\n")

    def check_evolution(self) -> list[dict[str, Any]]:
        """检查是否有达到阈值的序列模式。

        Returns:
            新生成的 Skill 列表（供 Agent 写入 SKILL.md）。
        """
        sequences = self._load_sequences()
        if len(sequences) < self.EVOLVE_THRESHOLD:
            return []

        # 按工具组合签名聚类
        patterns: Counter = Counter()
        examples: dict[str, list[str]] = {}  # signature → task descriptions
        for seq in sequences:
            sig = " → ".join(seq.tools)
            patterns[sig] += 1
            if sig not in examples:
                examples[sig] = []
            if seq.task_description:
                examples[sig].append(seq.task_description)

        # 找出 ≥ 阈值的模式（跳过已处理的）
        new_skills: list[dict[str, Any]] = []
        existing_skills = self._load_existing_skill_names()

        for sig, count in patterns.most_common():
            if count < self.EVOLVE_THRESHOLD:
                continue
            if self.is_processed(sig):
                continue
            tools_list = sig.split(" → ")
            name = "-".join(tools_list[:3]).replace("_", "-")[:40]
            if name in existing_skills:
                continue

            new_skills.append({
                "name": name, "signature": sig, "count": count,
                "tools": tools_list,
                "example_tasks": examples.get(sig, [])[:3],
            })

        return new_skills

    # ── 使用计数 ────────────────────────────────────────────────────

    def increment_usage(self, skill_name: str) -> None:
        """已存在的 Skill 被使用，计数+1。"""
        skill_file = _skills_dir() / f"{skill_name}.md"
        if not skill_file.exists():
            return
        try:
            text = skill_file.read_text(encoding="utf-8")
            # 简单替换：usage_count: N → N+1
            import re
            def _inc(m):
                return f"usage_count: {int(m.group(1)) + 1}"
            new_text = re.sub(r"usage_count:\s*(\d+)", _inc, text)
            skill_file.write_text(new_text, encoding="utf-8")
        except Exception:
            pass

    # ── 内部 ─────────────────────────────────────────────────────────

    def _load_sequences(self) -> list[ToolSequence]:
        """从 observations.jsonl 加载所有序列。"""
        f = _obs_file()
        if not f.exists():
            return []
        seqs: list[ToolSequence] = []
        try:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                d = json.loads(line)
                seqs.append(ToolSequence(
                    tools=d.get("tools", []),
                    task_description=d.get("task", ""),
                    success=d.get("success", True),
                    timestamp=d.get("timestamp", 0),
                ))
        except Exception:
            pass
        return seqs

    @staticmethod
    def _load_existing_skill_names() -> set[str]:
        """加载已有的 Skill 名（避免重复生成）。"""
        names: set[str] = set()
        d = _skills_dir()
        if d.exists():
            for md in d.glob("*.md"):
                names.add(md.stem)
        return names

    @property
    def observation_count(self) -> int:
        """已记录的观测序列数。"""
        f = _obs_file()
        if not f.exists():
            return 0
        try:
            return len(f.read_text(encoding="utf-8").strip().split("\n"))
        except Exception:
            return 0
