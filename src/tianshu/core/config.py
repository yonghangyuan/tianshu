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


def resolve_config_dir(project_root: Path | None = None) -> Path:
    """解析 config/ 目录：pip 安装场景下项目根没有 config/，回退到 ~/.tianshu/config/。

    优先级: TIANSHU_CONFIG_DIR 环境变量 > project_root/config（存在时）
    > ~/.tianshu/config（不存在则返回此路径，由调用方决定是否引导初始化）。
    """
    env_dir = os.environ.get("TIANSHU_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    if project_root is not None:
        candidate = project_root / "config"
        if candidate.is_dir():
            return candidate
    return Path.home() / ".tianshu" / "config"


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


def load_mcp_config(
    config_path: str | Path = "config/mcp.yaml",
) -> dict[str, Any]:
    """从 YAML 加载 MCP server 配置。

    两层合并：用户级 ~/.tianshu/mcp.yaml (优先) + 项目级 config/mcp.yaml。
    文件不存在不报错——MCP 是可选的。

    Args:
        config_path: 项目级 mcp.yaml 的路径

    Returns:
        {"servers": {name: {transport, url/command, ...}}}
    """
    merged: dict[str, Any] = {}

    # 1. 项目级配置
    project_path = Path(config_path)
    if project_path.exists():
        with open(project_path, "r", encoding="utf-8") as f:
            project_config = yaml.safe_load(f) or {}
        merged = project_config

    # 2. 用户级配置（覆盖项目级）
    user_path = Path.home() / ".tianshu" / "mcp.yaml"
    if user_path.exists():
        with open(user_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        # 深层合并：servers 级别按 server name 覆盖
        user_servers = user_config.get("servers", {})
        if "servers" not in merged:
            merged["servers"] = {}
        merged["servers"].update(user_servers)

    # 3. 解析 ${ENV_VAR} 占位符
    merged = _resolve_mcp_env(merged)

    return merged


def _resolve_mcp_env(config: dict) -> dict:
    """递归解析 MCP 配置中的 ${ENV_VAR} 占位符。"""
    if isinstance(config, dict):
        return {k: _resolve_mcp_env(v) for k, v in config.items()}
    elif isinstance(config, list):
        return [_resolve_mcp_env(item) for item in config]
    elif isinstance(config, str):
        return _resolve_env(config)
    return config


def load_rag_config(
    config_path: str | Path = "config/rag.yaml",
) -> dict[str, Any]:
    """从 YAML 加载 RAG 知识库配置。

    两层合并：用户级 ~/.tianshu/rag.yaml (优先) + 项目级 config/rag.yaml。
    文件不存在不报错——RAG 降级为离线 Mock 模式。

    Returns:
        {embedding: {...}, storage: {...}, chunking: {...}, search: {...}}
    """
    merged: dict[str, Any] = {}

    # 1. 项目级配置
    project_path = Path(config_path)
    if project_path.exists():
        with open(project_path, "r", encoding="utf-8") as f:
            merged = yaml.safe_load(f) or {}

    # 2. 用户级配置（覆盖项目级）
    user_path = Path.home() / ".tianshu" / "rag.yaml"
    if user_path.exists():
        with open(user_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        for k, v in user_config.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v

    # 3. 解析 ${ENV_VAR} 占位符
    return _resolve_mcp_env(merged)


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
