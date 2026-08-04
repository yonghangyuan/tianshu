"""天枢 SDK — 所有服务的共享数据模型和接口。

微服务架构中的"共享内核"——每个服务都依赖此包，
包含所有跨服务通信的数据结构和抽象接口。
"""

from .models import (
    # Provider
    ToolCall, TokenUsage, ProviderResponse,
    # Permission
    PermissionLevel,
    # Skill
    SkillDef, SkillTool,
    # Audit
    AuditLevel, AuditRecord, ProvenanceInput, ProvenanceCommand, ProvenanceEvaluation,
    # Agent
    AgentRequest, AgentResponse, AgentTurn, AgentContext,
    # Streaming
    StreamEvent, ContentDelta, ReasoningDelta, ToolCallStart,
    ToolCallResult, ToolCallConfirm, StreamDone, StreamError,
)

from .trigram import (
    # Layer
    Layer, LayerPermission,
    # Message
    TrigramMessage, MessagePriority, MessageDirection,
    AgentRef, MessageConstraints,
    # Time
    TimeScale, InfoDecayConfig, SyncMode,
    # Audit
    AuditSixQuestions, AuditCompleteness,
    # Registration
    AgentRegistration,
    # Scenarios
    urban_city_brain_agents, industrial_iot_agents,
    # Bayesian Fusion
    EntityDynamics, SensorCharacteristics,
    bayesian_fuse, FusedEstimate,
    update_sensor_reliability,
    # Arbitration (legacy)
    arbitrate, ArbitrationResult,
    # Decision Engine
    DecisionCriterion, DecisionContext, DecisionResult,
    select_criterion, decide,
    # Validation
    validate_message,
)

from .provider import BaseProvider

__all__ = [
    # Provider
    "ToolCall", "TokenUsage", "ProviderResponse", "BaseProvider",
    # Permission
    "PermissionLevel",
    # Skill
    "SkillDef", "SkillTool",
    # Audit (models)
    "AuditLevel", "AuditRecord", "ProvenanceInput", "ProvenanceCommand", "ProvenanceEvaluation",
    # Audit (trigram)
    "AuditSixQuestions", "AuditCompleteness",
    # Agent
    "AgentRequest", "AgentResponse", "AgentTurn", "AgentContext",
    # Streaming
    "StreamEvent", "ContentDelta", "ReasoningDelta", "ToolCallStart",
    "ToolCallResult", "ToolCallConfirm", "StreamDone", "StreamError",
    # Trigram — Layer
    "Layer", "LayerPermission",
    # Trigram — Message
    "TrigramMessage", "MessagePriority", "MessageDirection",
    "AgentRef", "MessageConstraints",
    # Trigram — Time
    "TimeScale", "InfoDecayConfig", "SyncMode",
    # Trigram — Registration
    "AgentRegistration",
    # Trigram — Scenarios
    "urban_city_brain_agents", "industrial_iot_agents",
    # Trigram — Arbitration
    "arbitrate", "ArbitrationResult",
    # Trigram — Decision Engine
    "DecisionCriterion", "DecisionContext", "DecisionResult",
    "select_criterion", "decide",
    # Trigram — Validation
    "validate_message",
]
