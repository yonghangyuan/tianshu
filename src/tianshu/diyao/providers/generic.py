"""通用 OpenAI 兼容 Provider — 适配任何 OpenAI 格式的 API。

用于 Kimi（月之暗面）/ GLM（智谱）/ Qwen / MiniMax / 百川 等
所有实现了 /v1/chat/completions 接口的国内模型。
"""

from __future__ import annotations

from .base import OpenAICompatibleProvider


class GenericOpenAIProvider(OpenAICompatibleProvider):
    """通用的 OpenAI 兼容 API 适配器。

    用于尚未有专属适配器的模型。只要 API 支持
    POST /v1/chat/completions 且返回标准 OpenAI 格式即可使用。
    """

    def __init__(
        self,
        provider_name: str,
        model_id: str,
        api_key: str | None = None,
        base_url: str = "",
        api_key_env: str = "",
        tags: list[str] | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._api_key_env = api_key_env or f"{provider_name.upper()}_API_KEY"
        self._tags = set(tags or [])

        import os
        self._api_key = api_key or os.environ.get(self._api_key_env, "")

    @property
    def capabilities(self) -> set[str]:
        """providers.yaml 模型级 tags（含 no_tools 等行为标记）。"""
        return set(self._tags)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def api_key_env(self) -> str:
        return self._api_key_env
