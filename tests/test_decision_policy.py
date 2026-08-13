"""决策策略引擎测试——天权决策的执行前治理闸门 (Step 6)。

授权书 (天权内部) 管「能做什么」，决策策略 (天枢) 管「绝对不能做什么」。
"""

from __future__ import annotations

import pytest

from tianshu.core.policy_engine import (
    DecisionPolicyRule,
    PolicyEngine,
)


def make_engine(tmp_path) -> PolicyEngine:
    """构造带决策策略的 PolicyEngine。"""
    import yaml
    config = {
        "decision_policies": [
            {
                "name": "no-prod-autonomy",
                "domain": "prod-*",
                "action": "confirm",
                "message": "生产环境决策需人工确认",
            },
            {
                "name": "high-stake-confirm",
                "domain": "*",
                "max_stake": 1000,
                "action": "confirm",
                "message": "利害超限需人工确认",
            },
            {
                "name": "irreversible-deny",
                "domain": "*",
                "max_reversibility": "reversible",
                "action": "deny",
                "message": "不可逆决策禁止自主执行",
            },
        ]
    }
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.dump(config), encoding="utf-8")
    engine = PolicyEngine()
    engine.load(str(path))
    return engine


def make_decision(**kw) -> dict:
    """构造标准决策 dict (天权 → 天枢的接口格式)。"""
    d = {
        "decision_id": "D-test1234",
        "domain": "server-ops",
        "stake": {"amount": 300.0, "unit": "CNY"},
        "reversibility": "reversible",
        "option": "A",
        "confidence": 0.78,
    }
    d.update(kw)
    return d


class TestDecisionPolicy:
    def test_low_risk_decision_passes(self, tmp_path):
        """低利害可逆决策 → 不命中任何规则 → pass。"""
        engine = make_engine(tmp_path)
        d = engine.evaluate_decision(make_decision())
        assert d.action == "pass"
        assert not d.matched

    def test_prod_domain_triggers_confirm(self, tmp_path):
        engine = make_engine(tmp_path)
        d = engine.evaluate_decision(make_decision(domain="prod-web"))
        assert d.action == "confirm"
        assert d.matched
        assert d.policy_name == "no-prod-autonomy"

    def test_high_stake_triggers_confirm(self, tmp_path):
        engine = make_engine(tmp_path)
        d = engine.evaluate_decision(
            make_decision(stake={"amount": 5000.0, "unit": "CNY"})
        )
        assert d.action == "confirm"
        assert d.policy_name == "high-stake-confirm"

    def test_irreversible_triggers_deny(self, tmp_path):
        engine = make_engine(tmp_path)
        d = engine.evaluate_decision(make_decision(reversibility="irreversible"))
        assert d.action == "deny"
        assert d.policy_name == "irreversible-deny"

    def test_first_matching_rule_wins(self, tmp_path):
        """规则按声明顺序: prod + irreversible → 先命中 prod 规则。"""
        engine = make_engine(tmp_path)
        d = engine.evaluate_decision(
            make_decision(domain="prod-web", reversibility="irreversible")
        )
        assert d.policy_name == "no-prod-autonomy"

    def test_rule_count_includes_decisions(self, tmp_path):
        engine = make_engine(tmp_path)
        assert engine.rule_count == 3
        assert engine.enabled


class TestDecisionPolicyRule:
    def test_domain_wildcard(self, tmp_path):
        """domain fnmatch 通配: finance-* 只匹配 finance 域。"""
        import yaml
        config = {
            "decision_policies": [
                {"name": "finance-gate", "domain": "finance-*",
                 "action": "deny", "message": "财务决策禁"},
            ]
        }
        path = tmp_path / "policy.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        engine = PolicyEngine()
        engine.load(str(path))

        assert engine.evaluate_decision(make_decision(domain="finance-pay")).action == "deny"
        assert engine.evaluate_decision(make_decision(domain="server-ops")).action == "pass"


class TestCompatibility:
    def test_tool_policies_still_work(self, tmp_path):
        """工具策略和决策策略共存互不影响。"""
        import yaml
        config = {
            "policies": [
                {
                    "name": "no-dangerous",
                    "rule": {"tool_in": ["shell_exec"], "command_contains": ["rm"]},
                    "action": "deny",
                    "message": "危险命令",
                }
            ],
            "decision_policies": [
                {"name": "d1", "domain": "*", "action": "deny", "message": "禁"}
            ],
        }
        path = tmp_path / "policy.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        engine = PolicyEngine()
        engine.load(str(path))

        # 工具策略
        assert engine.evaluate("shell_exec", {"command": "rm -rf /"}).action == "deny"
        # 决策策略
        assert engine.evaluate_decision(make_decision()).action == "deny"
        # 工具未命中
        assert engine.evaluate("browse", {"url": "https://x.com"}).action == "pass"

    def test_empty_config(self, tmp_path):
        """无决策策略时, evaluate_decision 返回 pass。"""
        engine = PolicyEngine()
        engine.load(str(tmp_path / "nonexistent.yaml"))
        d = engine.evaluate_decision(make_decision())
        assert d.action == "pass"
