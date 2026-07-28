"""天枢 SDK — 所有服务的共享数据模型和接口。

微服务架构中的"共享内核"——每个服务都依赖此包，
包含所有跨服务通信的数据结构和抽象接口。
"""

from .models import (
    # Provider
    ToolCall, TokenUsage, ProviderResponse,
    # Skill
    SkillDef, SkillTool,
    # Audit
    AuditLevel, AuditRecord, ProvenanceInput, ProvenanceCommand, ProvenanceEvaluation,
    # Agent
    AgentRequest, AgentResponse, AgentTurn, AgentContext,
)

from .provider import BaseProvider

__all__ = [
    # Provider
    "ToolCall", "TokenUsage", "ProviderResponse", "BaseProvider",
    # Skill
    "SkillDef", "SkillTool",
    # Audit
    "AuditLevel", "AuditRecord", "ProvenanceInput", "ProvenanceCommand", "ProvenanceEvaluation",
    # Agent
    "AgentRequest", "AgentResponse", "AgentTurn", "AgentContext",
]
