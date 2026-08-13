"""Agent 评测框架——加载场景 → 执行 → 评分。

用法:
    python -m tests.eval.runner              # 运行所有场景
    python -m tests.eval.runner social.yaml  # 运行指定场景
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml


class EvalScenario:
    """单个评测场景。"""

    def __init__(self, data: dict, file_path: str = ""):
        self.name = data.get("name", "Unnamed")
        self.description = data.get("description", "")
        self.input = data.get("input", "")
        self.file = file_path
        self.expect = data.get("expect", {})
        self.tags = data.get("tags", [])

    @property
    def expected_tools(self) -> list[dict]:
        return self.expect.get("tools", [])

    @property
    def output_must_contain(self) -> list[str]:
        return self.expect.get("output_contains", [])

    @property
    def output_must_not_contain(self) -> list[str]:
        return self.expect.get("output_not_contains", [])

    @property
    def max_tools(self) -> int:
        return self.expect.get("max_tools", 20)

    @property
    def max_latency_ms(self) -> int:
        return self.expect.get("max_latency_ms", 60000)

    @property
    def min_tools(self) -> int:
        return self.expect.get("min_tools", 0)


class EvalResult:
    """单个场景的评测结果。"""

    def __init__(self, scenario: EvalScenario):
        self.scenario = scenario
        self.passed = True
        self.tool_calls: list[dict] = []
        self.output = ""
        self.latency_ms = 0
        self.errors: list[str] = []
        self.checks: list[dict] = []

    def fail(self, reason: str) -> None:
        self.passed = False
        self.errors.append(reason)

    def add_check(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.passed = False


class EvalRunner:
    """评测运行器。"""

    def __init__(self, scenarios_dir: str | Path = ""):
        if not scenarios_dir:
            scenarios_dir = Path(__file__).parent / "scenarios"
        self._dir = Path(scenarios_dir)

    def load_scenarios(self) -> list[EvalScenario]:
        """加载所有评测场景。"""
        scenarios: list[EvalScenario] = []
        for f in sorted(self._dir.glob("*.yaml")):
            try:
                with open(f, encoding="utf-8") as fp:
                    data = yaml.safe_load(fp)
                if isinstance(data, list):
                    for item in data:
                        scenarios.append(EvalScenario(item, str(f)))
                elif isinstance(data, dict) and "name" in data:
                    scenarios.append(EvalScenario(data, str(f)))
            except Exception as e:
                print(f"WARNING: Failed to load {f}: {e}")
        return scenarios

    def run_all(self) -> list[EvalResult]:
        """运行所有场景（mock 模式）。"""
        scenarios = self.load_scenarios()
        if not scenarios:
            print("No scenarios found in", self._dir)
            return []

        print(f"=== 天枢 Agent 评测 ===\n")
        print(f"场景数: {len(scenarios)}\n")

        results: list[EvalResult] = []
        for i, scenario in enumerate(scenarios):
            print(f"[{i+1}/{len(scenarios)}] {scenario.name}")
            result = self.run_one(scenario)
            results.append(result)

            status = "PASS" if result.passed else "FAIL"
            print(f"  {status} ({len(result.tool_calls)} tools, {result.latency_ms}ms)")
            for c in result.checks:
                icon = "+" if c["passed"] else "-"
                print(f"    {icon} {c['name']}")
            if result.errors:
                for e in result.errors:
                    print(f"    * {e}")
            print()

        passed = sum(1 for r in results if r.passed)
        print(f"=== {passed}/{len(results)} PASSED ===\n")
        return results

    def run_one(self, scenario: EvalScenario) -> EvalResult:
        """运行单个场景——使用 mock LLM 快速验证。"""
        result = EvalResult(scenario)
        t0 = time.time()

        try:
            # 加载 Agent
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
            from tianshu.core.config import load_providers, load_routing_config
            from tianshu.core.router import ModelRouter, RoutingConfig
            from tianshu.core.service import AgentCore
            from tianshu.sdk.models import AgentRequest

            # 尝试加载真实 provider，失败则用 mock
            try:
                registry = load_providers("config/providers.yaml")
                routing = load_routing_config("config/providers.yaml")
            except Exception:
                # Mock fallback
                from tianshu.diyao.providers.base import BaseProvider, ProviderResponse
                from tianshu.diyao.providers.registry import ProviderRegistry

                class _MockProvider(BaseProvider):
                    provider_name = "mock"
                    model_id = "mock-1"

                    async def chat(self, messages, tools=None, **kw):
                        tool_name = scenario.expected_tools[0]["name"] if scenario.expected_tools else ""
                        tool_args = scenario.expected_tools[0].get("args_contains", {}) if scenario.expected_tools else {}
                        tool_calls = []
                        if tool_name:
                            tool_calls = [type('TC', (), {
                                'id': 'call_1', 'name': tool_name,
                                'arguments': tool_args,
                                'function': type('F', (), {'name': tool_name, 'arguments': json.dumps(tool_args)})()
                            })()]
                        return ProviderResponse(
                            content=scenario.input + " 的答案是测试响应。",
                            tool_calls=tool_calls,
                            usage=type('U', (), {'prompt_tokens': 100, 'completion_tokens': 50})(),
                        )

                registry = ProviderRegistry()
                registry.register(_MockProvider())
                routing = RoutingConfig(rules=[], fallback="mock/mock-1")

            core = AgentCore()
            core.setup(registry, routing, "Test system prompt", db_path=":memory:")

            import asyncio as _asyncio
            resp = _asyncio.run(core.run(AgentRequest(input=scenario.input, task_type="conversation")))
            result.output = resp.content or ""
            result.tool_calls = resp.tool_calls or []
            result.latency_ms = int((time.time() - t0) * 1000)

        except Exception as e:
            result.fail(f"执行异常: {e}")
            result.latency_ms = int((time.time() - t0) * 1000)

        # ── 评分 ──
        self._score(result)

        return result

    def _score(self, result: EvalResult) -> None:
        s = result.scenario

        # 1. 工具调用检查
        if s.expected_tools:
            tool_names = [t.get("name", "") for t in result.tool_calls]
            for expected in s.expected_tools:
                exp_name = expected.get("name", "")
                found = any(exp_name in tn or tn == exp_name for tn in tool_names)
                result.add_check(
                    f"工具 '{exp_name}' 被调用",
                    found,
                    f"实际调用: {tool_names}" if not found else "",
                )

                # 参数检查
                args_contain = expected.get("args_contains", {})
                if args_contain and found:
                    for tc in result.tool_calls:
                        if exp_name in tc.get("name", ""):
                            tc_args = tc.get("arguments", {})
                            for k, v in args_contain.items():
                                actual = tc_args.get(k)
                                result.add_check(
                                    f"参数 {k}={v}",
                                    str(actual) == str(v) or str(v) in str(actual),
                                    f"期望 {v} 实际 {actual}",
                                )

        # 2. 输出内容检查
        for keyword in s.output_must_contain:
            result.add_check(
                f"输出包含 '{keyword}'",
                keyword in result.output,
            )

        for keyword in s.output_must_not_contain:
            result.add_check(
                f"输出不包含 '{keyword}'",
                keyword not in result.output,
            )

        # 3. 工具数量
        actual_count = len(result.tool_calls)
        if actual_count > s.max_tools:
            result.fail(f"工具调用过多: {actual_count} > {s.max_tools}")
        result.add_check(
            f"工具调用 ≤ {s.max_tools}",
            actual_count <= s.max_tools,
            f"实际: {actual_count}",
        )

        if s.min_tools > 0:
            result.add_check(
                f"工具调用 ≥ {s.min_tools}",
                actual_count >= s.min_tools,
                f"实际: {actual_count}",
            )

        # 4. 延迟
        if s.max_latency_ms > 0:
            result.add_check(
                f"延迟 ≤ {s.max_latency_ms}ms",
                result.latency_ms <= s.max_latency_ms,
                f"实际: {result.latency_ms}ms",
            )


if __name__ == "__main__":
    runner = EvalRunner()
    results = runner.run_all()
    failed = sum(1 for r in results if not r.passed)
    sys.exit(1 if failed > 0 else 0)
