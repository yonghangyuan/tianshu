"""Provider 抽象接口 — 借鉴 Hermes ProviderTransport 设计。

核心原则：
  - 模型选择权完全交给用户
  - 每个 provider 暴露 capabilities 标签供路由匹配
  - OpenAI 兼容接口优先（覆盖 90% 国内模型）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """LLM 返回的工具调用。"""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class TokenUsage:
    """Token 用量统计。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # DeepSeek 专属：缓存的 prompt token（命中 Prompt Cache 则 >0）
    cached_prompt_tokens: int = 0
    # DeepSeek Reasoner 专属：推理 token
    reasoning_tokens: int = 0


@dataclass
class ProviderResponse:
    """统一的模型响应。"""
    content: str | None
    tool_calls: list[ToolCall] | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = "stop"
    # API 返回的实际 model 名 — 证明用的是哪个模型
    actual_model: str = ""
    # DeepSeek v4 thinking 模式的推理过程
    reasoning_content: str = ""
    # 模型原始响应（调试用）
    raw: dict[str, Any] | None = None


@dataclass
class ProviderStreamChunk:
    """流式响应的单个 chunk —— chat_stream() 的 yield 单元。

    每个 chunk 只包含增量（delta），由上层拼接。
    """
    delta_content: str = ""
    # 增量 tool_calls（OpenAI streaming 格式，可能跨多个 chunk）
    tool_call_deltas: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    reasoning_content: str = ""


class BaseProvider(ABC):
    """模型适配器统一接口。

    每个 provider 实现此接口，封装特定模型 API 的细节。
    借鉴 Hermes ProviderTransport：薄薄一层适配，不做复杂逻辑。
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ) -> ProviderResponse:
        """发送消息到模型，返回统一响应。

        Args:
            messages: OpenAI 格式的消息列表 [{"role": "...", "content": "..."}]
            tools: OpenAI 格式的工具定义列表
            temperature: 采样温度
            max_tokens: 最大输出 token 数

        Returns:
            ProviderResponse: 统一的模型响应
        """
        ...

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ):
        """流式发送消息到模型，逐 chunk yield。

        默认实现：回退到非流式 chat()，将完整响应包装为单个 chunk。
        子类（OpenAICompatibleProvider）应覆写为真正的 SSE streaming。
        """
        from collections.abc import AsyncGenerator
        resp = await self.chat(messages, tools, temperature, max_tokens)
        yield ProviderStreamChunk(
            delta_content=resp.content or "",
            finish_reason=resp.finish_reason,
            usage=resp.usage,
            reasoning_content=resp.reasoning_content,
        )

    @abstractmethod
    async def is_available(self) -> bool:
        """检查 provider 当前是否可用（健康检查）。"""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider 标识名，如 "deepseek"、"doubao"。"""
        ...

    @property
    @abstractmethod
    def model_id(self) -> str:
        """当前使用的模型 ID，如 "deepseek-chat"。"""
        ...

    @property
    def capabilities(self) -> set[str]:
        """能力标签。子类可覆写。

        常见标签：reasoning | long_context | fast | cheap | coding | multilingual | deep_think
        """
        return set()

    @property
    def max_context_tokens(self) -> int:
        """最大上下文窗口 token 数。子类应覆写。"""
        return 65536

    def __repr__(self) -> str:
        return f"{self.provider_name}/{self.model_id}"


# ── OpenAI 兼容 Provider 基类 ─────────────────────────────────────────────

class OpenAICompatibleProvider(BaseProvider):
    """OpenAI 兼容 API 的通用基类。

    所有支持 OpenAI chat/completions 格式的国内模型
    （DeepSeek / 豆包 / Kimi / GLM / Qwen / MiniMax ...）
    只需覆写 base_url、model_id、api_key_env 三个属性即可。

    其他差异通过钩子方法处理：
      - _build_body()     —— 构造请求体（DS 可加 cache_control）
      - _parse_response() —— 解析响应（DS 可提取 reasoning_content）
      - _build_headers()  —— 构造请求头
    """

    def __init__(self, api_key: str | None = None) -> None:
        import os
        self._api_key = api_key or os.environ.get(self.api_key_env, "")

    # ── 子类必须覆写 ──

    @property
    @abstractmethod
    def base_url(self) -> str:
        """API 基础 URL。如 https://api.deepseek.com/v1"""
        ...

    @property
    @abstractmethod
    def api_key_env(self) -> str:
        """API Key 环境变量名。如 DEEPSEEK_API_KEY"""
        ...

    # ── 子类可选覆写的钩子 ──

    def _build_headers(self) -> dict[str, str]:
        """构造请求头。DS/豆包/Kimi 默认都一样。"""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """构造请求体。子类可覆写以注入 provider 专属参数。"""
        # 本地推理（Ollama 等 localhost 端点）不限费，思考模型（qwen3/
        # deepseek-r1）思维链动辄数千 token——放大预算避免正文被饿死；
        # 云端按调用方给定值计（省钱纪律）
        if any(h in self.base_url for h in ("127.0.0.1", "localhost")):
            max_tokens = max(max_tokens, 16384)
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        return body

    def _parse_response(self, raw: dict[str, Any]) -> ProviderResponse:
        """解析 OpenAI 兼容格式的响应。"""
        choice = raw["choices"][0]
        message = choice.get("message", {})

        # 解析 tool_calls
        tool_calls = None
        if message.get("tool_calls"):
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=tc["function"].get("arguments", {}),
                )
                for tc in message["tool_calls"]
            ]

        # 解析 usage
        usage_raw = raw.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
            total_tokens=usage_raw.get("total_tokens", 0),
            # Prompt Cache 命中 token 数（DeepSeek 等厂商；未命中恒为 0）
            cached_prompt_tokens=usage_raw.get("prompt_cache_hit_tokens", 0),
        )

        return ProviderResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop"),
            actual_model=raw.get("model", ""),
            # Ollama qwen3 系非流式用 "reasoning"（流式 delta 同名）
            reasoning_content=message.get("reasoning_content", "")
            or message.get("reasoning", ""),
            raw=raw,
        )

    # ── 通用实现 ──

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ) -> ProviderResponse:
        import httpx

        body = self._build_body(messages, tools, temperature, max_tokens)
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=headers,
            )
            if resp.status_code >= 400:
                # 捕获错误响应体中的详细信息
                try:
                    detail = resp.json()
                except Exception:
                    detail = resp.text[:300]
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} {resp.reason_phrase}: {detail}",
                    request=resp.request,
                    response=resp,
                )
            return self._parse_response(resp.json())

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ):
        """SSE 流式请求 —— 逐 chunk 解析 OpenAI 兼容 streaming 格式。

        格式：
          data: {"choices":[{"delta":{"content":"你好"},"index":0}]}
          data: {"choices":[{"delta":{"tool_calls":[...]},"index":0}]}
          data: [DONE]

        对每个 SSE data 行，yield 一个 ProviderStreamChunk。
        """
        import json as _json
        import httpx

        body = self._build_body(messages, tools, temperature, max_tokens)
        body["stream"] = True
        headers = self._build_headers()
        headers["Accept"] = "text/event-stream"

        accumulated_tool_calls: dict[int, dict[str, Any]] = {}
        final_usage: TokenUsage | None = None
        final_finish: str | None = None

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=body,
                headers=headers,
            ) as resp:
                if resp.status_code >= 400:
                    # 读取错误体
                    try:
                        detail = await resp.aread()
                        detail_str = detail.decode()[:500]
                    except Exception:
                        detail_str = str(resp.status_code)
                    raise httpx.HTTPStatusError(
                        f"{resp.status_code}: {detail_str}",
                        request=resp.request,
                        response=resp,
                    )

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue

                    data_str = line[6:]  # 去掉 "data: " 前缀
                    if data_str == "[DONE]":
                        break

                    try:
                        data = _json.loads(data_str)
                    except _json.JSONDecodeError:
                        continue

                    chunk = self._parse_stream_chunk(
                        data, accumulated_tool_calls
                    )
                    if chunk is None:
                        continue

                    # 收集最终 usage
                    if chunk.usage:
                        final_usage = chunk.usage
                    if chunk.finish_reason:
                        final_finish = chunk.finish_reason

                    yield chunk

                # 发送最终统计 chunk
                if final_usage or final_finish:
                    yield ProviderStreamChunk(
                        finish_reason=final_finish,
                        usage=final_usage,
                    )

    def _parse_stream_chunk(
        self,
        data: dict[str, Any],
        accumulated_tool_calls: dict[int, dict[str, Any]],
    ) -> "ProviderStreamChunk | None":
        """解析单个 SSE data JSON → ProviderStreamChunk。

        子类可覆写以提取 provider 专属字段（如 reasoning_content）。
        """
        choices = data.get("choices", [])
        if not choices:
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        finish = choice.get("finish_reason")

        # 内容 delta
        content = delta.get("content", "") or ""

        # reasoning_content（DeepSeek 等）；Ollama qwen3 系用 "reasoning"
        reasoning = delta.get("reasoning_content", "") or delta.get("reasoning", "") or ""

        # 增量 tool_calls
        raw_tool_deltas = delta.get("tool_calls")
        tool_deltas = None
        if raw_tool_deltas:
            tool_deltas = []
            for tc in raw_tool_deltas:
                idx = tc.get("index", 0)
                if idx not in accumulated_tool_calls:
                    accumulated_tool_calls[idx] = {
                        "id": "", "name": "", "arguments": ""
                    }
                acc = accumulated_tool_calls[idx]
                if tc.get("id"):
                    acc["id"] = tc["id"]
                func = tc.get("function", {})
                if func.get("name"):
                    acc["name"] += func["name"]
                if func.get("arguments"):
                    acc["arguments"] += func["arguments"]
                tool_deltas.append({
                    "index": idx,
                    "id": acc["id"],
                    "name": acc["name"],
                    "arguments": acc["arguments"],
                })

        # usage（最后一个 chunk 可能带）
        usage_raw = data.get("usage")
        usage = None
        if usage_raw:
            usage = TokenUsage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
                # Prompt Cache 命中 token 数（流式路径——DeepSeek 不覆写
                # _parse_stream_chunk，此洞必须在这里补）
                cached_prompt_tokens=usage_raw.get("prompt_cache_hit_tokens", 0),
            )

        # 如果没有任何有效内容，跳过
        if not content and not reasoning and not tool_deltas and not finish:
            # 只检查 usage
            if not usage:
                return None

        return ProviderStreamChunk(
            delta_content=content,
            tool_call_deltas=tool_deltas,
            finish_reason=finish,
            usage=usage,
            reasoning_content=reasoning,
        )

    async def is_available(self) -> bool:
        """发送最小请求验证可用性。"""
        try:
            await self.chat(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except Exception as e:
            self._last_error = str(e)[:200]
            return False

    @property
    def last_error(self) -> str:
        return getattr(self, "_last_error", "")
