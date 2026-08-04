"""Context Engine — 独立化的上下文组装引擎。

从 service.py 的 _build_messages() 抽离而来。
负责: system prompt 拼装 + 历史注入 + 分级压缩 + 记忆注入。
对外单一接口: assemble()。
"""

from __future__ import annotations

import time
from typing import Any

from ..sdk.models import AgentContext


class ContextEngine:
    """上下文组装引擎——将所有进入 LLM 的消息源统一管理。

    用法:
        engine = ContextEngine(system_prompt)
        messages, comp_meta = await engine.assemble(
            user_input, ctx.messages, provider, audit_service,
        )
    """

    def __init__(self, system_prompt: str = ""):
        self.system_prompt = system_prompt

    async def assemble(
        self,
        user_input: str,
        history: list[dict[str, Any]],
        provider: Any = None,
        audit_service: Any = None,
        *,
        provider_info: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """组装完整的消息列表。

        Returns:
            (messages, compression_meta | None)
        """
        messages: list[dict[str, Any]] = []

        # 1. System prompt
        if self.system_prompt:
            prompt = self.system_prompt
            if provider_info:
                prompt = (
                    f"## Current Session\n"
                    f"You are answering via the **{provider_info}** model. "
                    f"If asked which model you are, say the model name directly.\n\n"
                    + prompt
                )
            messages.append({"role": "system", "content": prompt})

        # 2. History + user input
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        # 3. Compression (if needed)
        comp_meta = None
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        estimated_tokens = int(total_chars * 0.4)
        max_tokens = provider.max_context_tokens if provider else 65536
        ratio = estimated_tokens / max_tokens if max_tokens > 0 else 0.0

        if ratio > 0.5 and provider and len(history) > 6:
            comp_meta = await self._compress(
                messages, ratio, provider, audit_service,
                estimated_tokens, max_tokens, total_chars,
            )
            if comp_meta and "_messages" in comp_meta:
                messages = comp_meta.pop("_messages")

        return messages, comp_meta

    # ── 内部 ─────────────────────────────────────────────────────

    async def _compress(
        self,
        messages: list[dict],
        ratio: float,
        provider: Any,
        audit_service: Any,
        estimated_tokens: int,
        max_tokens: int,
        total_chars: int,
    ) -> dict | None:
        """分级压缩。"""
        # Compression level
        if ratio > 0.9:
            comp_level, tail_count, max_summary = 3, 2, 150
        elif ratio > 0.7:
            comp_level, tail_count, max_summary = 2, 4, 250
        else:
            comp_level, tail_count, max_summary = 1, 6, 300

        head = messages[:1]
        tail = messages[-tail_count:]
        middle = messages[1:-tail_count]

        if not middle:
            return None

        try:
            middle_text = "\n".join(
                f"[{m['role']}] {str(m.get('content', ''))[:300]}"
                for m in middle
            )
            summary = await provider.chat(
                messages=[{
                    "role": "user",
                    "content": (
                        f"将以下对话中间部分总结为三段，用中文：\n"
                        f"1. [已完成] 用户已解决的问题\n"
                        f"2. [待处理] 尚未完成的事项\n"
                        f"3. [关键信息] 后续可能需要引用的重要事实\n"
                        f"每段不超过3条。简洁精准。\n\n"
                        f"对话:\n{middle_text}"
                    ),
                }],
                max_tokens=max_summary,
            )
            summary_text = summary.content or "[压缩失败]"

            compressed_content = (
                f"[上下文已压缩 L{comp_level}: {len(middle)}条→{max_summary}tok]\n"
                f"{summary_text[:max_summary + 50]}"
            )
            compressed = head + [{"role": "system", "content": compressed_content}] + tail

            # Store audit snapshot
            comp_decision_id = ""
            if audit_service:
                try:
                    comp_decision_id = audit_service.generate_id()
                    await audit_service.store_snapshot(
                        comp_decision_id,
                        {
                            "type": "context_compression",
                            "level": comp_level,
                            "ratio": round(ratio, 2),
                            "before_chars": total_chars,
                            "after_chars": sum(
                                len(str(m.get("content", ""))) for m in compressed
                            ),
                            "summary": summary_text,
                            "timestamp": time.time(),
                        },
                    )
                except Exception:
                    comp_decision_id = "db_migration_needed"

            return {
                "_messages": compressed,
                "level": comp_level,
                "ratio": round(ratio, 2),
                "before_chars": total_chars,
                "after_chars": sum(
                    len(str(m.get("content", ""))) for m in compressed
                ),
                "summary": summary_text[:200],
                "stored_decision_id": comp_decision_id,
            }
        except Exception:
            return None
