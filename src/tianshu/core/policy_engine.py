"""天爻策略引擎 —— 决策预执行检查。

在工具执行前，逐条评估 config/policy.yaml 中的策略规则。
支持三种动作：deny（拒绝）· confirm（弹窗确认）· allow（放行）。

与天曜审计集成：每次策略决策（命中/未命中）都记录到 audit_records。
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PolicyDecision:
    """一条策略评估结果。"""
    policy_name: str
    action: str  # deny | confirm | allow | pass
    message: str = ""
    matched: bool = False


@dataclass
class PolicyRule:
    """一条策略规则。"""
    name: str
    description: str = ""
    rule: dict = field(default_factory=dict)
    action: str = "allow"
    message: str = ""


class PolicyEngine:
    """策略引擎 —— 工具执行前的最后一道防线。

    用法:
        engine = PolicyEngine("config/policy.yaml")
        decision = engine.evaluate("shell_exec", {"command": "rm -rf /"})
        if decision.action == "deny":
            return f"Blocked: {decision.message}"
    """

    def __init__(self, config_path: str = ""):
        self._rules: list[PolicyRule] = []
        self._enabled = False
        if config_path:
            self.load(config_path)

    def load(self, config_path: str) -> int:
        """加载策略文件。返回规则数。"""
        path = Path(config_path)
        if not path.exists():
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            return 0

        self._rules.clear()
        for item in data.get("policies", []):
            self._rules.append(PolicyRule(
                name=item.get("name", ""),
                description=item.get("description", ""),
                rule=item.get("rule", {}),
                action=item.get("action", "allow"),
                message=item.get("message", ""),
            ))

        self._enabled = len(self._rules) > 0
        return len(self._rules)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def evaluate(self, tool_name: str, tool_args: dict[str, Any]) -> PolicyDecision:
        """评估一条工具调用是否合规。

        策略按优先级评估，第一个命中的规则决定结果。
        未命中任何规则 → action="pass"。

        Args:
            tool_name: 工具名称 (e.g. "shell_exec")
            tool_args: 工具参数 (e.g. {"command": "ls"})

        Returns:
            PolicyDecision with action + message
        """
        if not self._enabled:
            return PolicyDecision(policy_name="", action="pass", matched=False)

        for rule in self._rules:
            if self._match(rule.rule, tool_name, tool_args):
                return PolicyDecision(
                    policy_name=rule.name,
                    action=rule.action,
                    message=rule.message,
                    matched=True,
                )

        # 未命中任何规则 = 放行
        return PolicyDecision(policy_name="", action="pass", matched=False)

    def _match(self, rule: dict, tool_name: str, tool_args: dict) -> bool:
        """检查 tool_name + tool_args 是否匹配一条 rule。"""
        # 1. tool_in: 工具名在列表中
        if "tool_in" in rule:
            if tool_name not in rule["tool_in"]:
                return False

        # 2. command_contains: 命令包含关键字（shell_exec 专用）
        if "command_contains" in rule:
            command = tool_args.get("command", "")
            if not any(kw.lower() in command.lower()
                       for kw in rule["command_contains"]):
                return False

        # 3. path_match: 路径匹配 glob 模式
        if "path_match" in rule:
            path = tool_args.get("path", tool_args.get("local_path", ""))
            if not path:
                return False
            # 支持列表
            patterns = rule["path_match"]
            if isinstance(patterns, str):
                patterns = [patterns]
            if not any(fnmatch.fnmatch(path.lower(), p.lower())
                       for p in patterns):
                return False

        # 4. url_not_match: URL 不包含任何允许的域名 → 触发（用于境外检测）
        if "url_not_match" in rule:
            url = tool_args.get("url", "")
            if not url:
                return False
            patterns = rule["url_not_match"]
            if isinstance(patterns, str):
                patterns = [patterns]
            # 如果 URL 包含任何允许的域名 → 不触发
            url_lower = url.lower()
            if any(p.strip("*").lower() in url_lower for p in patterns):
                return False

        return True

    def list_rules(self) -> list[dict]:
        """返回所有规则的人类可读摘要。"""
        return [
            {
                "name": r.name,
                "description": r.description,
                "action": r.action,
            }
            for r in self._rules
        ]
