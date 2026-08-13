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


@dataclass
class DecisionPolicyRule:
    """一条决策策略规则——天权决策的执行前治理闸门。

    与工具策略的区别: 工具策略管「怎么调用工具」，
    决策策略管「天权能自主执行什么决策」。
    授权书 (天权内部) 管「能做什么」，决策策略 (天枢) 管「绝对不能做什么」。
    """
    name: str
    description: str = ""
    domain: str = "*"                  # fnmatch 模式匹配决策域
    max_stake: float | None = None     # 利害上限 (同单位比较)
    max_reversibility: str | None = None  # reversible | irreversible
    action: str = "deny"
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
        self._decision_rules: list[DecisionPolicyRule] = []
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

        # 决策策略 (天权决策的执行前治理闸门)
        self._decision_rules.clear()
        for item in data.get("decision_policies", []):
            self._decision_rules.append(DecisionPolicyRule(
                name=item.get("name", ""),
                description=item.get("description", ""),
                domain=item.get("domain", "*"),
                max_stake=item.get("max_stake"),
                max_reversibility=item.get("max_reversibility"),
                action=item.get("action", "deny"),
                message=item.get("message", ""),
            ))

        self._enabled = len(self._rules) > 0 or len(self._decision_rules) > 0
        return len(self._rules) + len(self._decision_rules)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def rule_count(self) -> int:
        return len(self._rules) + len(self._decision_rules)

    @property
    def decision_rule_count(self) -> int:
        return len(self._decision_rules)

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

    def evaluate_decision(self, decision: dict[str, Any]) -> PolicyDecision:
        """评估一个天权决策能否执行——治理闸门。

        决策格式 (纯 dict, 天枢不依赖天权包):
        {
            "decision_id": "D-abc12345",
            "domain": "server-ops",
            "stake": {"amount": 300.0, "unit": "CNY"},
            "reversibility": "reversible",   # reversible | irreversible
            "option": "A",
            "confidence": 0.78,
        }

        规则按声明顺序评估，第一个命中的规则决定结果。
        未命中 → pass (授权书已在天权侧校验过，这里只做绝对禁区)。
        """
        for rule in self._decision_rules:
            if not self._match_decision(rule, decision):
                continue
            return PolicyDecision(
                policy_name=rule.name,
                action=rule.action,
                message=rule.message,
                matched=True,
            )
        return PolicyDecision(policy_name="", action="pass", matched=False)

    def _match_decision(self, rule: DecisionPolicyRule, decision: dict) -> bool:
        """检查决策是否匹配一条决策策略规则。"""
        # 1. 决策域匹配
        domain = decision.get("domain", "")
        if not fnmatch.fnmatch(domain, rule.domain):
            return False

        # 2. 利害上限 (amount 超过上限 → 匹配)
        stake = decision.get("stake", {})
        amount = stake.get("amount", 0) if isinstance(stake, dict) else 0
        if rule.max_stake is not None:
            if amount < rule.max_stake:
                return False  # 未超限 → 此规则不适用

        # 3. 可逆性上限 (风险序: reversible < irreversible)
        # 决策风险 ≤ 允许上限 → 规则不适用；超出 → 匹配
        reversibility = decision.get("reversibility", "reversible")
        if rule.max_reversibility is not None:
            risk = {"reversible": 0, "irreversible": 1}
            if risk.get(reversibility, 0) <= risk.get(rule.max_reversibility, 0):
                return False

        return True

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
