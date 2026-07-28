"""Provider 注册中心 — 管理所有模型适配器实例。"""

from __future__ import annotations

from .base import BaseProvider


class ProviderRegistry:
    """Provider 注册中心。

    在启动时根据 config/providers.yaml 注册所有 provider 实例，
    运行时由 ModelRouter 通过此注册中心查询可用模型。
    """

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, BaseProvider]] = {}

    def register(self, provider: BaseProvider) -> None:
        """注册一个 provider 实例。

        同一个 provider_name 可以有多个 model（如 deepseek/chat 和 deepseek/reasoner）。
        """
        name = provider.provider_name
        if name not in self._providers:
            self._providers[name] = {}
        self._providers[name][provider.model_id] = provider

    def get(self, provider_name: str, model_id: str | None = None) -> BaseProvider | None:
        """获取指定 provider 的实例。

        Args:
            provider_name: 如 "deepseek"
            model_id: 如 "deepseek-chat"。如果为 None，返回该 provider 的默认模型。

        Returns:
            Provider 实例，不存在则返回 None。
        """
        models = self._providers.get(provider_name, {})
        if not models:
            return None
        if model_id:
            return models.get(model_id)
        # 返回第一个注册的模型作为默认
        return next(iter(models.values()))

    async def get_available(self, provider_name: str, model_id: str) -> BaseProvider | None:
        """获取 provider 并验证可用性。不可用则返回 None。"""
        provider = self.get(provider_name, model_id)
        if provider and await provider.is_available():
            return provider
        return None

    def list_all(self) -> list[BaseProvider]:
        """列出所有已注册的 provider 实例。"""
        result: list[BaseProvider] = []
        for models in self._providers.values():
            result.extend(models.values())
        return result

    def list_provider_names(self) -> list[str]:
        """列出所有已注册的 provider 名称。"""
        return list(self._providers.keys())

    def __repr__(self) -> str:
        count = sum(len(m) for m in self._providers.values())
        names = ", ".join(self.list_provider_names())
        return f"ProviderRegistry({count} models: {names})"
