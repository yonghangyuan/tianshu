"""天枢 SDK — Provider 抽象接口。

所有模型适配器实现此接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import ProviderResponse


class BaseProvider(ABC):
    """模型适配器统一接口。

    所有 provider（DeepSeek / 豆包 / Kimi / GLM / ...）
    必须实现此接口。这是天枢与任何 LLM 通信的唯一通道。
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        """发送消息到模型，返回统一响应。"""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """健康检查——模型当前是否可调用。"""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider 标识名，如 "deepseek"。"""
        ...

    @property
    @abstractmethod
    def model_id(self) -> str:
        """当前使用的模型 ID，如 "deepseek-v4-pro"。"""
        ...

    @property
    def capabilities(self) -> set[str]:
        """能力标签：reasoning | fast | cheap | coding | long_context | multilingual。"""
        return set()

    @property
    def max_context_tokens(self) -> int:
        return 65536

    @property
    def last_error(self) -> str:
        return getattr(self, "_last_error", "")

    def __repr__(self) -> str:
        return f"{self.provider_name}/{self.model_id}"
