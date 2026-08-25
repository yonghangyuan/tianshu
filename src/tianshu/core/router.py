"""模型路由 — 完全由用户配置驱动，框架不做任何预设。

路由逻辑：
  1. 读 config/providers.yaml 的 routing.rules
  2. 按 task_type 匹配规则 → 得到 prefer 列表
  3. 从 prefer 列表中选第一个可用的 provider+model
  4. 全部不可用 → fallback
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from ..diyao.providers.base import BaseProvider
from ..diyao.providers.registry import ProviderRegistry


class AuditLevel(IntEnum):
    """天爻审计级别。"""
    BASIC = 1       # ID + 时间戳
    SNAPSHOT = 2    # + 世界状态快照
    FULL = 3        # + 完整推理链
    EVALUATED = 4   # + 事后评估回填


@dataclass
class RoutingRule:
    """一条路由规则。"""
    task_types: list[str]
    prefer: list[str]  # ["deepseek/chat", "doubao/lite-128k"]


@dataclass
class RoutingConfig:
    """路由配置。"""
    rules: list[RoutingRule] = field(default_factory=list)
    fallback: str = "deepseek/chat"


class ModelRouter:
    """配置驱动的模型路由器。"""

    def __init__(self, config: RoutingConfig, registry: ProviderRegistry) -> None:
        self._rules = config.rules
        self._fallback = config.fallback
        self._registry = registry

    async def route(self, task_type: str) -> tuple[BaseProvider | None, str, AuditLevel]:
        """根据任务类型路由到最合适的模型。

        Args:
            task_type: 任务类型标签（translation, deep_analysis 等）

        Returns:
            (provider, model_id, audit_level)
            如果所有 provider 都不可用，provider 为 None。
        """
        # 1. 匹配规则 — 直接用第一个注册的模型，不做预检
        #    预检发真实 API 请求太慢且不可靠，改为"先调，失败再降级"。
        for rule in self._rules:
            if task_type in rule.task_types:
                for pref in rule.prefer:
                    provider_name, model_id = self._parse_pref(pref)
                    provider_name, model_id = self._resolve_pref(provider_name, model_id)
                    provider = self._registry.get(provider_name, model_id)
                    if provider is not None:
                        return provider, model_id, self._audit_level(task_type)

        # 2. fallback
        fb_provider_name, fb_model_id = self._parse_pref(self._fallback)
        fb_provider_name, fb_model_id = self._resolve_pref(fb_provider_name, fb_model_id)
        provider = self._registry.get(fb_provider_name, fb_model_id)
        return provider, fb_model_id, self._audit_level(task_type)

    def _resolve_pref(self, provider_name: str, model_id: str) -> tuple[str, str]:
        """prefer 条目解析：原样命中优先，未命中再补厂商前缀（同 route_direct）。"""
        if self._registry.get(provider_name, model_id) is not None:
            return provider_name, model_id
        prefix = f"{provider_name}-"
        if not model_id.startswith(prefix):
            model_id = prefix + model_id
        return provider_name, model_id

    @staticmethod
    def _parse_pref(pref: str) -> tuple[str, str]:
        """拆分 "deepseek/chat" → ("deepseek", "chat")。

        只拆不改写——补前缀统一由 _resolve_pref 做（原样命中优先，
        避免误伤 Ollama 的 "llama3.2:latest" 这类自带族名+tag 的 id）。
        """
        parts = pref.split("/", 1)
        name = parts[0]
        model = parts[1] if len(parts) > 1 else ""
        return name, model

    @staticmethod
    def _audit_level(task_type: str) -> AuditLevel:
        """根据任务类型确定审计级别。"""
        DEEP = {"deep_analysis", "reasoning", "code_generation",
                "architecture", "review", "planning"}
        if task_type in DEEP:
            return AuditLevel.FULL
        return AuditLevel.BASIC

    def route_direct(self, provider_name: str, model_id: str) -> tuple[BaseProvider | None, str, AuditLevel]:
        """用户直接指定模型，不走路由规则。

        Args:
            provider_name: 如 "deepseek"、"ollama"
            model_id: 如 "v4-pro" 或 "deepseek-v4-pro"

        Returns:
            (provider, full_model_id, audit_level)
        """
        # 先按原始 id 查（Ollama 等自带族名+tag 的 id 不该被改写：
        # "llama3.2:latest" 补成 "ollama-llama3.2:latest" 必查空）
        provider = self._registry.get(provider_name, model_id)
        if provider is not None:
            return provider, model_id, AuditLevel.FULL
        # 查不到再补前缀（云端厂商简写：v4-pro → deepseek-v4-pro）
        prefix = f"{provider_name}-"
        full_model_id = model_id if model_id.startswith(prefix) else prefix + model_id
        provider = self._registry.get(provider_name, full_model_id)
        return provider, full_model_id, AuditLevel.FULL

    def describe_route(self, task_type: str) -> str:
        """人类可读的路由说明（调试用）。"""
        for rule in self._rules:
            if task_type in rule.task_types:
                return f"{task_type} → {' > '.join(rule.prefer)} (fallback: {self._fallback})"
        return f"{task_type} → fallback: {self._fallback}"
