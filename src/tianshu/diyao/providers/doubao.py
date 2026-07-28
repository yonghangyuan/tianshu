"""豆包（字节跳动火山引擎）API 适配器。

豆包 Ark API 完全兼容 OpenAI 格式，只需覆写 base_url。
"""

from __future__ import annotations

from .generic import GenericOpenAIProvider


class DoubaoProvider(GenericOpenAIProvider):
    """豆包 API 适配器。"""

    def __init__(self, model: str = "doubao-lite-128k", api_key: str | None = None,
                 base_url: str = "") -> None:
        base_url = base_url or "https://ark.cn-beijing.volces.com/api/v3"
        super().__init__(
            provider_name="doubao",
            model_id=model,
            api_key=api_key,
            base_url=base_url,
            api_key_env="DOUBAO_API_KEY",
        )

    @property
    def capabilities(self) -> set[str]:
        caps = {"multilingual", "long_context"}
        if "lite" in self._model_id:
            caps.update({"fast", "cheap"})
        elif "pro" in self._model_id:
            caps.add("reasoning")
        return caps
