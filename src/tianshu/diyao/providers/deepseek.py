"""DeepSeek API 适配器 — P0 定向优化。

DeepSeek 专属优化点：
  1. Prompt Cache —— DS 按消息前缀匹配缓存，命中后 prompt_tokens 中
     cached 部分只收 ¥0.01/百万 token（原价 ¥1/百万 token）
  2. Reasoning Token —— deepseek-reasoner 的 reasoning_content 字段单独捕获
     用于天爻审计的推理链记录
  3. Tool Use —— DS 的 tool_calls 返回格式与标准 OpenAI 有细微差异，
     function.arguments 可能是 JSON 字符串而非对象
  4. 长上下文 —— DS 支持 64K 窗口，chat 模型和 reasoner 都支持
"""

from __future__ import annotations

from typing import Any

from .base import OpenAICompatibleProvider, ProviderResponse, TokenUsage


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek API 适配器。

    支持模型：
      - deepseek-v4-pro    (旗舰，1M 上下文，thinking 模式)
      - deepseek-v4-flash  (快速，1M 上下文)
      - deepseek-chat      (经典，即将弃用)
      - deepseek-reasoner  (经典推理，即将弃用)
    """

    base_url = "https://api.deepseek.com/v1"
    api_key_env = "DEEPSEEK_API_KEY"

    def __init__(self, model: str = "deepseek-chat", api_key: str | None = None) -> None:
        super().__init__(api_key=api_key)
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "deepseek"

    # ── v4 专属参数 ─────────────────────────────────────────────────

    def _build_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """DeepSeek v4 专属：注入 thinking + reasoning_effort 参数。"""
        body = super()._build_body(messages, tools, temperature, max_tokens)

        # v4 模型需要 thinking 参数
        if "v4" in self._model:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "high"

        return body

    # ── capabilities ─────────────────────────────────────────────────

    @property
    def capabilities(self) -> set[str]:
        caps = {"coding", "multilingual"}
        if "reasoner" in self._model:
            caps.add("reasoning")
            caps.add("deep_think")
        else:
            caps.add("fast")
            caps.add("cheap")
        return caps

    @property
    def max_context_tokens(self) -> int:
        return 65536  # DS 所有模型都是 64K

    # ── P0-1: Prompt Cache 检测 ────────────────────────────────────────

    # DeepSeek 的 Prompt Cache 机制：
    #   - 按消息前缀自动匹配（不需要显式 cache_control）
    #   - 命中缓存的 token 在 usage 中记为 prompt_cache_hit_tokens
    #   - 价格：缓存的 prompt ¥0.01/百万 token，未缓存的 ¥1/百万 token
    #   - 所以系统提示词应该放在 messages 最前面，且在不同请求间保持完全一致

    def _parse_response(self, raw: dict[str, Any]) -> ProviderResponse:
        """解析 DS 响应，额外捕获缓存命中和推理 token。"""
        choice = raw["choices"][0]
        message = choice.get("message", {})

        # Tool calls（DS 格式：arguments 可能是 JSON 字符串）
        from .base import ToolCall as TC
        tool_calls = None
        if message.get("tool_calls"):
            tool_calls = []
            for tc in message["tool_calls"]:
                args = tc["function"].get("arguments", {})
                # DS 有时返回 JSON 字符串而非对象 → 统一解析为 dict
                if isinstance(args, str):
                    import json
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        pass
                tool_calls.append(
                    TC(id=tc["id"], name=tc["function"]["name"], arguments=args)
                )

        # Usage + 缓存检测
        usage_raw = raw.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
            total_tokens=usage_raw.get("total_tokens", 0),
            # P0-1: Prompt Cache 命中 token 数
            cached_prompt_tokens=usage_raw.get("prompt_cache_hit_tokens", 0),
            # P0-2: Reasoning token（reasoner 模型）
            reasoning_tokens=usage_raw.get("completion_tokens_details", {}).get(
                "reasoning_tokens", 0
            ),
        )

        # P0-2: Reasoning content 捕获
        content = message.get("content")
        reasoning_content = message.get("reasoning_content")

        # 如果有推理内容，拼入 content 前缀（天爻审计用）
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls or None,
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop"),
            actual_model=raw.get("model", ""),
            reasoning_content=reasoning_content or "",
            raw=raw,
        )

    # ── P0-3: Prompt Cache 优化辅助 ───────────────────────────────────

    @staticmethod
    def estimate_cache_savings(usage: TokenUsage) -> float:
        """估算本次调用因 Prompt Cache 节省的费用（人民币）。

        DS 定价：
          - 缓存命中 prompt: ¥0.01/百万 token
          - 未命中 prompt:   ¥1.00/百万 token（chat）/ ¥4.00/百万 token（reasoner）
        """
        if usage.cached_prompt_tokens == 0:
            return 0.0

        # 假设 chat 模型价格
        normal_price = 1.0  # ¥/百万 token
        cached_price = 0.01

        saved = usage.cached_prompt_tokens * (normal_price - cached_price) / 1_000_000
        return round(saved, 4)
