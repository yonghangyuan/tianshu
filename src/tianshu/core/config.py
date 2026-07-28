"""配置加载器 — 从 YAML 配置文件构建 ProviderRegistry + ModelRouter。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from ..diyao.providers.base import BaseProvider
from ..diyao.providers.deepseek import DeepSeekProvider
from ..diyao.providers.registry import ProviderRegistry
from .router import ModelRouter, RoutingConfig, RoutingRule


def _resolve_env(value: str) -> str:
    """解析 ${ENV_VAR} 占位符。"""
    pattern = re.compile(r"\$\{(\w+)\}")
    matches = pattern.findall(value)
    for var in matches:
        env_val = os.environ.get(var, "")
        value = value.replace(f"${{{var}}}", env_val)
    return value


def load_providers(
    config_path: str | Path = "config/providers.yaml",
    extra_keys: dict[str, str] | None = None,
) -> ProviderRegistry:
    """从 YAML 配置文件加载所有 provider 并注册。

    Args:
        config_path: providers.yaml 的路径

    Returns:
        已注册所有 provider 的 ProviderRegistry 实例

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置格式错误
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"模型配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    registry = ProviderRegistry()

    providers_section = config.get("providers", {})
    extra_keys = extra_keys or {}

    for name, cfg in providers_section.items():
        # 优先级: 环境变量 > 本地 ~/.tianshu/config.yaml > 配置文件默认值
        api_key = _resolve_env(cfg.get("api_key", ""))
        if not api_key:
            api_key = extra_keys.get(name, "")
        base_url = cfg.get("base_url", "")

        for model_cfg in cfg.get("models", []):
            model_id = model_cfg["id"]
            provider = _create_provider(name, model_id, api_key, base_url)
            if provider:
                registry.register(provider)

    return registry


def load_routing_config(config_path: str | Path = "config/providers.yaml") -> RoutingConfig:
    """从 YAML 配置文件加载路由规则。

    Returns:
        RoutingConfig 实例
    """
    config_path = Path(config_path)
    if not config_path.exists():
        return RoutingConfig(fallback="deepseek/chat")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    routing = config.get("routing", {})
    rules = [
        RoutingRule(task_types=r["task_types"], prefer=r["prefer"])
        for r in routing.get("rules", [])
    ]
    fallback = routing.get("fallback", "deepseek/chat")

    return RoutingConfig(rules=rules, fallback=fallback)


def _create_provider(
    name: str, model_id: str, api_key: str, base_url: str
) -> BaseProvider | None:
    """根据 provider 名称创建对应的适配器实例。"""
    if name == "deepseek":
        return DeepSeekProvider(model=model_id, api_key=api_key)
    elif name == "doubao":
        from ..diyao.providers.doubao import DoubaoProvider
        return DoubaoProvider(model=model_id, api_key=api_key, base_url=base_url)
    elif name in ("moonshot", "kimi"):
        from ..diyao.providers.generic import GenericOpenAIProvider
        return GenericOpenAIProvider(
            provider_name=name, model_id=model_id,
            api_key=api_key, base_url=base_url,
        )
    elif name == "zhipu":
        from ..diyao.providers.generic import GenericOpenAIProvider
        return GenericOpenAIProvider(
            provider_name="zhipu", model_id=model_id,
            api_key=api_key, base_url=base_url,
        )
    else:
        # 未知 provider → 尝试通用 OpenAI 兼容适配器
        from ..diyao.providers.generic import GenericOpenAIProvider
        return GenericOpenAIProvider(
            provider_name=name, model_id=model_id,
            api_key=api_key, base_url=base_url,
        )
