"""天枢 SDK — 统一数据模型。

所有服务共享这些数据结构。修改需向后兼容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Provider 相关
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ToolCall:
    """LLM 返回的工具调用。"""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenUsage:
    """Token 用量统计。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class ProviderResponse:
    """统一的模型响应——所有 provider 适配器返回此结构。"""
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = "stop"
    actual_model: str = ""
    reasoning_content: str = ""  # DeepSeek v4 thinking 模式的推理过程
    raw: dict[str, Any] | None = None


# ═══════════════════════════════════════════════════════════════════════════
# Skill 相关
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SkillTool:
    """Skill 提供的工具定义。"""
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    permission_level: int = 0  # PermissionLevel

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class SkillDef:
    """Skill 元数据。"""
    name: str
    description: str = ""
    trigram: str = "人"
    tool_names: list[str] = field(default_factory=list)
    trigger_keywords: list[str] = field(default_factory=list)
    version: int = 1
    usage_count: int = 0
    created: str = "manual"
    source_path: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# 审计相关
# ═══════════════════════════════════════════════════════════════════════════

class AuditLevel(IntEnum):
    BASIC = 1
    SNAPSHOT = 2
    FULL = 3
    EVALUATED = 4


class PermissionLevel(IntEnum):
    """工具操作的风险级别。

    SAFE:   只读、无副作用（web_search, recall_memory）
    READ:   读取本地资源（文件读取、目录列表）
    WRITE:  修改本地资源（文件写入、shell_exec）
    DANGER: 系统级操作（rm -rf, chmod, 网络出站到未知地址）
    """
    SAFE = 0
    READ = 1
    WRITE = 2
    DANGER = 3


@dataclass
class ProvenanceInput:
    """决策输入快照。"""
    data_source: str = ""
    source_id: str = ""
    content_hash: str = ""
    confidence: float = 1.0
    latency: float = 0.0
    path: list[str] = field(default_factory=list)
    timestamp_observed: float = 0.0


@dataclass
class ProvenanceCommand:
    """决策输出命令。"""
    command_type: str = ""
    target: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    comm_path: list[str] = field(default_factory=list)
    expected_outcome: str = ""


@dataclass
class ProvenanceEvaluation:
    """决策事后评估。"""
    actual_outcome: str = ""
    discrepancy: list[str] = field(default_factory=list)
    root_cause: str = ""
    feedback_action: str = ""


@dataclass
class AuditRecord:
    """一条审计记录。"""
    decision_id: str = ""
    timestamp: float = 0.0
    llm_model: str = ""
    llm_config: dict[str, Any] = field(default_factory=dict)
    system_prompt_version: str = ""
    available_tools: list[str] = field(default_factory=list)
    input_data: list[ProvenanceInput] = field(default_factory=list)
    output_commands: list[ProvenanceCommand] = field(default_factory=list)
    reasoning_chain: list[str] = field(default_factory=list)
    evaluation: ProvenanceEvaluation | None = None
    level: int = 1
    session_id: str = ""
    task_type: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Agent 请求/响应
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AgentRequest:
    """Agent Core API 请求。"""
    input: str
    session_id: str = ""
    model_override: str = ""
    task_type: str = "conversation"


@dataclass
class AgentResponse:
    """Agent Core API 响应。"""
    decision_id: str = ""
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    audit_level: int = 1
    model_used: str = ""
    elapsed_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""


@dataclass
class AgentTurn:
    """Agent 一轮对话的完整记录。"""
    decision_id: str
    user_input: str
    response: ProviderResponse | None = None
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    audit_level: AuditLevel = AuditLevel.BASIC


@dataclass
class AgentContext:
    """Agent 会话上下文——跨轮次共享。"""
    messages: list[dict[str, Any]] = field(default_factory=list)
    turns: list[AgentTurn] = field(default_factory=list)
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# Streaming 事件类型 — run_stream() 的 yield 单元
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class StreamEvent:
    """流式事件的基类。type 字段标识事件类型。"""
    type: str = ""


@dataclass
class ContentDelta(StreamEvent):
    """增量文本内容。"""
    type: str = "content_delta"
    text: str = ""


@dataclass
class ReasoningDelta(StreamEvent):
    """增量推理内容（thinking 模式）。"""
    type: str = "reasoning_delta"
    text: str = ""


@dataclass
class ToolCallStart(StreamEvent):
    """工具调用开始。"""
    type: str = "tool_call_start"
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_id: str = ""


@dataclass
class ToolCallResult(StreamEvent):
    """工具调用结果。"""
    type: str = "tool_call_result"
    tool_name: str = ""
    success: bool = True
    output: str = ""
    elapsed_ms: int = 0


@dataclass
class ToolCallConfirm(StreamEvent):
    """需要用户确认的工具调用。"""
    type: str = "tool_confirm"
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    permission_level: int = 0  # PermissionLevel


@dataclass
class StreamDone(StreamEvent):
    """流式响应完成。"""
    type: str = "done"
    decision_id: str = ""
    model_used: str = ""
    elapsed_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_count: int = 0
    error: str = ""


@dataclass
class StreamError(StreamEvent):
    """流式响应出错。"""
    type: str = "error"
    message: str = ""
    decision_id: str = ""
