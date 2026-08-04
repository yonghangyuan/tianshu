"""天枢三爻接口 — 跨层消息协议 + Agent 时间尺度 + 审计标准。

天·人·地三层不是命名空间，是三道不能互相绕过的闸门。
每一道闸门定义了清晰的输入/输出接口和权限边界。

与 models.py 的关系：
  trigram.py 是接口定义层，引用 models.py 中的基础类型
  (AuditRecord, AuditLevel, PermissionLevel, AgentContext 等)。
  未来 AgentCore 改造时，ReAct Loop 将走三层消息通道。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any
import time
import hashlib
import json


# ═══════════════════════════════════════════════════════════════════════════
# 一、三爻层级定义
# ═══════════════════════════════════════════════════════════════════════════


class Layer(Enum):
    """三爻——信息·能量·物质三条流的治理层。

    天 (TIAN): 规则引擎 + 冲突仲裁 + 跨时间尺度信息协调
        — 不直接执行任何操作，只裁定"能不能做"
        — 拥有最高 OVERRIDE 权限
        — 典型 Agent: 审计员、合规检查器、资源配额管理器、安全约束引擎
        — 时间尺度: 战略级（小时~年）

    人 (REN): 意图理解 + 优先级排序 + 多 Agent 调度 + 不可逆决策确认
        — 接收天层的规则约束，向下调度地层的执行
        — 拥有 URGENT 权限，但不能绕过天层的 OVERRIDE
        — 典型 Agent: 规划器、调度器、风险评估器、谈判Agent
        — 时间尺度: 运营级（分钟~小时）

    地 (DI): 传感器→数据流 + 执行器→命令流 + 物理世界接口
        — 执行上层下发的任务，向上报告执行结果和感知数据
        — 只能在赋予的权限范围内操作
        — 典型 Agent: 搜索器、浏览器、Shell执行器、传感器读取器、执行机构
        — 时间尺度: 战术级（毫秒~分钟）
    """

    TIAN = "tian"  # 天 — Governance
    REN = "ren"  # 人 — Decision
    DI = "di"  # 地 — Execution

    def label(self) -> str:
        """中文标签。"""
        return {Layer.TIAN: "天", Layer.REN: "人", Layer.DI: "地"}[self]

    def label_en(self) -> str:
        """英文标签。"""
        return {Layer.TIAN: "Governance", Layer.REN: "Decision", Layer.DI: "Execution"}[self]


# ═══════════════════════════════════════════════════════════════════════════
# 二、跨层消息协议 (Cross-Layer Message Protocol)
# ═══════════════════════════════════════════════════════════════════════════


class MessagePriority(IntEnum):
    """消息优先级。数值越大，优先级越高。

    NORMAL:   常规通信——定时报告、状态更新、低优先级查询
    URGENT:   需要立即响应——异常检测、威胁告警、超时预警
    OVERRIDE: 最高优先级——天层专属，用于否决下级决策、紧急制动
    """

    NORMAL = 0
    URGENT = 1
    OVERRIDE = 2


class MessageDirection(Enum):
    """消息流向——层间只能走相邻层，不可跨层直连。

    允许: 地↔人↔天
    禁止: 地→天 (必须经过人层)
    禁止: 天→地 (必须经过人层)
    """

    UP = "up"  # 地→人 或 人→天
    DOWN = "down"  # 天→人 或 人→地
    BROADCAST = "broadcast"  # 同层内广播


@dataclass
class AgentRef:
    """Agent 标识——全局唯一。"""

    layer: Layer
    agent_id: str
    instance_id: str = ""  # 同类型 Agent 的多实例区分

    def __str__(self) -> str:
        base = f"{self.layer.value}:{self.agent_id}"
        return f"{base}#{self.instance_id}" if self.instance_id else base

    def to_dict(self) -> dict[str, str]:
        return {
            "layer": self.layer.value,
            "agent_id": self.agent_id,
            "instance_id": self.instance_id,
        }


@dataclass
class MessageConstraints:
    """消息携带的硬约束——接收方必须遵守。"""

    time_budget_ms: int = 0  # 时间预算（0=无限制）
    permission_level: int = 0  # 最大允许的 PermissionLevel
    resource_limits: dict[str, Any] = field(default_factory=dict)
    # 示例: {"max_tokens": 10000, "max_tool_calls": 5, "allow_network": True}

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_budget_ms": self.time_budget_ms,
            "permission_level": self.permission_level,
            "resource_limits": self.resource_limits,
        }


@dataclass
class TrigramMessage:
    """三爻跨层消息——天·人·地之间的唯一通信格式。

    每一条消息都可以被天曜审计链追溯。消息本身是幂等的——
    同一个 decision_id 重复发送不产生副作用。

    示例:
        # 地层搜索Agent向人层规划Agent上报搜索结果
        TrigramMessage(
            decision_id="d_abc123",
            source=AgentRef(Layer.DI, "web_searcher"),
            target=AgentRef(Layer.REN, "planner"),
            intent="搜索完成，返回3条相关结果",
            payload={"results": [...], "query": "..."},
            direction=MessageDirection.UP,
        )

        # 天层合规Agent向人层下达否决
        TrigramMessage(
            decision_id="d_def456",
            source=AgentRef(Layer.TIAN, "compliance_checker"),
            target=AgentRef(Layer.REN, "planner"),
            intent="否决：拟执行的文件写入操作违反策略P3",
            priority=MessagePriority.OVERRIDE,
            direction=MessageDirection.DOWN,
        )
    """

    decision_id: str
    timestamp: float  # Unix ms
    ttl_ms: int  # 消息有效期(ms)，过期后置信度衰减
    source: AgentRef
    target: AgentRef
    intent: str  # 人类可读的意图描述
    payload: dict[str, Any] = field(default_factory=dict)
    constraints: MessageConstraints = field(default_factory=MessageConstraints)
    priority: MessagePriority = MessagePriority.NORMAL
    direction: MessageDirection = MessageDirection.UP
    audit_trail: list[dict[str, Any]] = field(default_factory=list)
    # audit_trail 中的每条记录: {"decision_id": str, "layer": str, "action": str, "timestamp": float}

    # ── 工厂方法 ─────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        source: AgentRef,
        target: AgentRef,
        intent: str,
        payload: dict | None = None,
        *,
        ttl_ms: int = 60_000,
        priority: MessagePriority = MessagePriority.NORMAL,
        constraints: MessageConstraints | None = None,
        audit_trail: list[dict] | None = None,
    ) -> TrigramMessage:
        """创建一条新消息——自动生成 decision_id 和时间戳。"""
        decision_id = _gen_decision_id()
        direction = _infer_direction(source.layer, target.layer)
        return cls(
            decision_id=decision_id,
            timestamp=int(time.time() * 1000),
            ttl_ms=ttl_ms,
            source=source,
            target=target,
            intent=intent,
            payload=payload or {},
            constraints=constraints or MessageConstraints(),
            priority=priority,
            direction=direction,
            audit_trail=audit_trail or [],
        )

    # ── 属性 ─────────────────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        """消息是否已过有效期。"""
        now_ms = int(time.time() * 1000)
        return (now_ms - self.timestamp) > self.ttl_ms

    @property
    def age_ms(self) -> int:
        """消息已存在时长(ms)。"""
        return int(time.time() * 1000) - int(self.timestamp)

    def confidence(self, half_life_ms: int | None = None) -> float:
        """信息置信度——基于时间衰减。

        当 half_life_ms 未指定时，使用 ttl_ms/2 作为半衰期。
        返回 0.0 ~ 1.0 之间的值。
        """
        hl = half_life_ms or (self.ttl_ms // 2)
        if hl <= 0:
            return 1.0
        import math

        return math.exp(-self.age_ms * math.log(2) / hl)

    # ── 序列化 ───────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "ttl_ms": self.ttl_ms,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "intent": self.intent,
            "payload": self.payload,
            "constraints": self.constraints.to_dict(),
            "priority": self.priority.name,
            "direction": self.direction.value,
            "audit_trail": self.audit_trail,
            "is_expired": self.is_expired,
            "age_ms": self.age_ms,
        }

    def __repr__(self) -> str:
        return (
            f"TrigramMsg({self.source}→{self.target} | "
            f"{self.priority.name} | {self.intent[:40]})"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 三、Agent 时间尺度模型
# ═══════════════════════════════════════════════════════════════════════════


class SyncMode(Enum):
    """Agent 间同步模式。

    PUSH:      状态变化时主动广播——适合高频、低延迟场景
    PULL:      按需轮询——适合低频、省资源场景
    THRESHOLD: 变化超过阈值才通知——适合数据量大的场景
    """

    PUSH = "push"
    PULL = "pull"
    THRESHOLD = "threshold"


@dataclass
class InfoDecayConfig:
    """信息时效衰减配置。

    可靠性 = base_confidence * exp(-t / half_life_ms * ln(2))

    示例：
      温度传感器: half_life_ms=5000  (5秒后半衰)
      卫星图像: half_life_ms=3600000  (1小时后半衰)
      日报数据: half_life_ms=86400000  (24小时后半衰)
    """

    half_life_ms: int  # 半衰期(ms)——经过此时间后置信度降为原始一半
    base_confidence: float = 1.0  # 初始置信度 (0~1)
    min_confidence: float = 0.1  # 最低置信度——不再继续衰减

    def confidence_at_age(self, age_ms: int) -> float:
        """计算给定时间后的置信度。"""
        import math

        if self.half_life_ms <= 0:
            return self.base_confidence
        decayed = self.base_confidence * math.exp(
            -age_ms * math.log(2) / self.half_life_ms
        )
        return max(decayed, self.min_confidence)


@dataclass
class TimeScale:
    """Agent 的时间尺度声明——每个 Agent 注册时必须指定。

    示例：
      # 产线传感器Agent——毫秒级
      TimeScale(tick_ms=100, decay=InfoDecayConfig(half_life_ms=5000), sync=SyncMode.PUSH)

      # 战略规划Agent——天级
      TimeScale(tick_ms=86400000, decay=InfoDecayConfig(half_life_ms=604800000), sync=SyncMode.PULL)
    """

    tick_ms: int  # 观察/行动间隔(ms)
    decay: InfoDecayConfig = field(
        default_factory=lambda: InfoDecayConfig(half_life_ms=60000)
    )
    sync: SyncMode = SyncMode.PUSH
    threshold: float = 0.0  # THRESHOLD 模式下的最小变化阈值
    max_staleness_ms: int = 0  # 数据最大允许陈旧时间(0=无限)

    @property
    def tick_label(self) -> str:
        """人类可读的 tick 频率。"""
        if self.tick_ms < 1000:
            return f"{self.tick_ms}ms"
        elif self.tick_ms < 60_000:
            return f"{self.tick_ms / 1000:.1f}s"
        elif self.tick_ms < 3_600_000:
            return f"{self.tick_ms / 60_000:.0f}min"
        elif self.tick_ms < 86_400_000:
            return f"{self.tick_ms / 3_600_000:.1f}h"
        else:
            return f"{self.tick_ms / 86_400_000:.1f}d"

    def is_stale(self, timestamp_ms: float) -> bool:
        """检查给定时间戳的数据是否已陈旧。"""
        if self.max_staleness_ms <= 0:
            return False
        return (int(time.time() * 1000) - int(timestamp_ms)) > self.max_staleness_ms


# ═══════════════════════════════════════════════════════════════════════════
# 四、审计标准
# ═══════════════════════════════════════════════════════════════════════════


class AuditCompleteness(IntEnum):
    """审计记录的完备程度。

    BASIC (L1):      只记录决策 ID + 时间戳 + 结果
    SNAPSHOT (L2):   + 决策时的系统状态快照
    FULL (L3):       + 推理链 + 考虑的替代方案
    EVALUATED (L4):  + 事后评估（决策是否正确，以及为什么）

    重要：审计不是事后记录，而是在决策前就写清楚"为什么"。
    L4 需要人类或独立 Agent 在事后进行评估。
    """

    BASIC = 1
    SNAPSHOT = 2
    FULL = 3
    EVALUATED = 4


@dataclass
class AuditSixQuestions:
    """一条合格的审计记录必须能回答这六个问题。

    用法：
      在决策点插入 AuditSixQuestions.record(...) 调用，
      确保每一个决策都有迹可循。
    """

    # 1. 谁做的决策？
    agent_ref: AgentRef

    # 2. 当时有什么信息可用？
    available_info: list[str] = field(default_factory=list)
    # ["传感器数据(t=12345,置信度0.9)", "巡检记录(t=12000,置信度0.7)"]
    info_snapshot_hash: str = ""  # 信息快照的哈希——用于事后校验是否被篡改

    # 3. 考虑了哪些替代方案？
    alternatives_considered: list[str] = field(default_factory=list)
    # ["方案A: 立即拦截(风险:高)", "方案B: 跟踪监视(风险:低)", "方案C: 不行动"]

    # 4. 什么约束在生效？
    active_rules: list[str] = field(default_factory=list)
    # ["SAFETY-3: 高温区域人员禁入", "QUOTA: 每日API调用限额500"]

    # 5. 结果如何？
    decision_made: str = ""  # 最终选择的方案
    outcome: str = ""  # 执行结果

    # 6. 能复现吗？
    replay_trace: list[dict[str, Any]] = field(default_factory=list)
    # 确定性的事件序列，用于事后复现决策过程

    @classmethod
    def record(
        cls,
        agent: AgentRef,
        info: list[str],
        alternatives: list[str],
        rules: list[str],
        decision: str,
        outcome: str = "",
        trace: list[dict] | None = None,
    ) -> AuditSixQuestions:
        """在决策点调用——记录一条审计快照。

        Returns:
            可序列化的审计快照，直接存入天曜审计链。
        """
        snapshot_data = json.dumps(
            {
                "info": info,
                "alternatives": alternatives,
                "rules": rules,
                "decision": decision,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return cls(
            agent_ref=agent,
            available_info=info,
            info_snapshot_hash=hashlib.sha256(snapshot_data.encode()).hexdigest()[:16],
            alternatives_considered=alternatives,
            active_rules=rules,
            decision_made=decision,
            outcome=outcome,
            replay_trace=trace or [],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "who": self.agent_ref.to_dict(),
            "info_available": self.available_info,
            "info_hash": self.info_snapshot_hash,
            "alternatives": self.alternatives_considered,
            "active_rules": self.active_rules,
            "decision": self.decision_made,
            "outcome": self.outcome,
            "replay_trace": self.replay_trace,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 五、层间权限与路由
# ═══════════════════════════════════════════════════════════════════════════


class LayerPermission(IntEnum):
    """层间操作的权限级别。

    天层拥有 ROOT 权限——可以否决任何下层的决策。
    人层拥有 MANAGE 权限——可以调度地层 Agent 但不能违反天层规则。
    地层拥有 EXECUTE 权限——只能在赋予的权限边界内执行。
    """

    EXECUTE = 0  # 地：只能在权限边界内执行
    MANAGE = 1  # 人：可调度、可优先排序
    OVERRULE = 2  # 天：可否决、可仲裁


def _infer_direction(from_layer: Layer, to_layer: Layer) -> MessageDirection:
    """根据源/目标层推断消息流向。"""
    order = {Layer.DI: 0, Layer.REN: 1, Layer.TIAN: 2}
    from_rank = order[from_layer]
    to_rank = order[to_layer]

    if from_rank == to_rank:
        return MessageDirection.BROADCAST
    elif from_rank < to_rank:
        return MessageDirection.UP
    else:
        return MessageDirection.DOWN


def _gen_decision_id() -> str:
    """生成全局唯一的 decision_id。"""
    import uuid

    return f"d_{uuid.uuid4().hex[:12]}"


def validate_message(msg: TrigramMessage) -> list[str]:
    """验证一条消息是否合法。返回错误列表（空列表=合法）。

    规则:
      1. 不能跨层直连（地↔天 需经过人层）
      2. OVERRIDE 优先级只能由天层发出
      3. URGENT 优先级不能由地层发出
    """
    errors: list[str] = []

    # 规则1: 禁止跨层直连
    if {msg.source.layer, msg.target.layer} == {Layer.DI, Layer.TIAN}:
        errors.append(
            f"跨层直连禁止: {msg.source.layer.value}→{msg.target.layer.value}"
            f"（必须经过人层）"
        )

    # 规则2: OVERRIDE 只能由天层发出
    if msg.priority == MessagePriority.OVERRIDE and msg.source.layer != Layer.TIAN:
        errors.append(
            f"OVERRIDE 优先级只能由天层发出，当前来源: {msg.source.layer.value}"
        )

    # 规则3: URGENT 不能由地层发出
    if msg.priority == MessagePriority.URGENT and msg.source.layer == Layer.DI:
        errors.append(
            f"URGENT 优先级不能由地层发出——地层应通过人层升级"
        )

    return errors


# ═══════════════════════════════════════════════════════════════════════════
# 六、Agent 注册信息
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class AgentRegistration:
    """Agent 在系统中注册时必须声明以下信息。

    用法：
      AgentRegistration(
          ref=AgentRef(Layer.DI, "temp_sensor", instance_id="workshop_3"),
          time_scale=TimeScale(tick_ms=100, decay=InfoDecayConfig(half_life_ms=5000)),
          permissions=[LayerPermission.EXECUTE],
          capabilities=["temperature_monitor", "anomaly_detect"],
          tool_names=["read_sensor", "check_threshold"],
      )
    """

    ref: AgentRef
    time_scale: TimeScale
    permissions: list[LayerPermission] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    description: str = ""
    parent_decision_id: str = ""  # 哪个决策创建了这个 Agent

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref.to_dict(),
            "time_scale": {
                "tick_ms": self.time_scale.tick_ms,
                "tick_label": self.time_scale.tick_label,
                "half_life_ms": self.time_scale.decay.half_life_ms,
                "sync_mode": self.time_scale.sync.value,
            },
            "permissions": [p.name for p in self.permissions],
            "capabilities": self.capabilities,
            "tool_names": self.tool_names,
            "description": self.description,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 七、场景实例化帮助函数
# ═══════════════════════════════════════════════════════════════════════════


def urban_city_brain_agents() -> list[AgentRegistration]:
    """城市大脑场景——示例 Agent 注册列表。"""
    return [
        AgentRegistration(
            ref=AgentRef(Layer.DI, "traffic_sensor"),
            time_scale=TimeScale(tick_ms=1000, decay=InfoDecayConfig(half_life_ms=30_000)),
            permissions=[LayerPermission.EXECUTE],
            capabilities=["vehicle_detection", "flow_count"],
            tool_names=["read_intersection", "count_vehicles"],
            description="路口交通传感器——每秒上报车流量",
        ),
        AgentRegistration(
            ref=AgentRef(Layer.DI, "power_grid_monitor"),
            time_scale=TimeScale(tick_ms=60_000, decay=InfoDecayConfig(half_life_ms=300_000)),
            permissions=[LayerPermission.EXECUTE],
            capabilities=["load_monitor", "fault_detection"],
            tool_names=["read_grid_load", "detect_outage"],
            description="电网负载监控——每分钟上报区域负载",
        ),
        AgentRegistration(
            ref=AgentRef(Layer.REN, "traffic_optimizer"),
            time_scale=TimeScale(tick_ms=30_000, decay=InfoDecayConfig(half_life_ms=120_000)),
            permissions=[LayerPermission.MANAGE],
            capabilities=["route_planning", "signal_optimization"],
            tool_names=["compute_optimal_phases", "adjust_signal_timing"],
            description="交通优化器——每30秒调整信号灯配时",
        ),
        AgentRegistration(
            ref=AgentRef(Layer.REN, "city_planner"),
            time_scale=TimeScale(
                tick_ms=86_400_000,  # 1天
                decay=InfoDecayConfig(half_life_ms=31_536_000_000),  # 1年
                sync=SyncMode.PULL,
            ),
            permissions=[LayerPermission.MANAGE],
            capabilities=["land_use", "population_modeling"],
            tool_names=["analyze_zoning", "forecast_growth"],
            description="城市规划师——每天评估土地利用方案",
        ),
        AgentRegistration(
            ref=AgentRef(Layer.TIAN, "resource_governor"),
            time_scale=TimeScale(
                tick_ms=3_600_000,  # 1小时
                decay=InfoDecayConfig(half_life_ms=86_400_000),
                sync=SyncMode.THRESHOLD,
                threshold=0.2,  # 资源变化超20%才通知
            ),
            permissions=[LayerPermission.OVERRULE],
            capabilities=["quota_enforcement", "fairness_arbitration"],
            tool_names=["check_quota", "arbitrate_conflict"],
            description="资源治理器——仲裁跨区域资源冲突",
        ),
    ]


def industrial_iot_agents() -> list[AgentRegistration]:
    """工业物联网场景——与城市大脑共享同一套接口，差异仅在 Agent 配置。

    演示时间异质性：产线传感器(ms级)、设备诊断(min级)、排产调度(h级)、
    安全约束引擎(实时 OVERRIDE)。
    """
    return [
        AgentRegistration(
            ref=AgentRef(Layer.DI, "vibration_sensor"),
            time_scale=TimeScale(
                tick_ms=100,  # 100ms
                decay=InfoDecayConfig(half_life_ms=5_000),  # 5秒半衰
                sync=SyncMode.PUSH,
            ),
            permissions=[LayerPermission.EXECUTE],
            capabilities=["vibration_monitor", "anomaly_detect"],
            tool_names=["read_vibration", "check_threshold"],
            description="振动传感器——100ms级采样，信息5秒半衰",
        ),
        AgentRegistration(
            ref=AgentRef(Layer.DI, "quality_inspector"),
            time_scale=TimeScale(
                tick_ms=3_600_000,  # 1小时
                decay=InfoDecayConfig(half_life_ms=10_800_000),  # 3小时半衰
                sync=SyncMode.THRESHOLD,
                threshold=0.3,
            ),
            permissions=[LayerPermission.EXECUTE],
            capabilities=["defect_detect", "quality_report"],
            tool_names=["inspect_batch", "generate_report"],
            description="质检分析——每小时抽检，异常超30%才上报",
        ),
        AgentRegistration(
            ref=AgentRef(Layer.REN, "production_scheduler"),
            time_scale=TimeScale(
                tick_ms=60_000,
                decay=InfoDecayConfig(half_life_ms=300_000),
                sync=SyncMode.PUSH,
            ),
            permissions=[LayerPermission.MANAGE],
            capabilities=["line_balancing", "order_priority"],
            tool_names=["adjust_schedule", "reassign_line"],
            description="排产调度——分钟级，动态调整产线分配",
        ),
        AgentRegistration(
            ref=AgentRef(Layer.REN, "supply_chain_planner"),
            time_scale=TimeScale(
                tick_ms=86_400_000,  # 1天
                decay=InfoDecayConfig(half_life_ms=604_800_000),  # 7天半衰
                sync=SyncMode.PULL,
            ),
            permissions=[LayerPermission.MANAGE],
            capabilities=["inventory_planning", "demand_forecast"],
            tool_names=["forecast_demand", "plan_procurement"],
            description="供应链规划——天级决策",
        ),
        AgentRegistration(
            ref=AgentRef(Layer.TIAN, "safety_gatekeeper"),
            time_scale=TimeScale(
                tick_ms=1_000,  # 1秒——安全约束检查必须实时
                decay=InfoDecayConfig(half_life_ms=3_600_000),
                sync=SyncMode.PUSH,
                max_staleness_ms=0,  # 不允许陈旧数据
            ),
            permissions=[LayerPermission.OVERRULE],
            capabilities=["safety_check", "emergency_stop", "zone_enforcement"],
            tool_names=["verify_safety", "trigger_estop", "log_override"],
            description="安全守门人——实时拦截违规操作，OVERRIDE权限",
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════════
# 八、贝叶斯信息融合 — 从二选一到多源融合
# ═══════════════════════════════════════════════════════════════════════════
#
# 设计原则（四层）：
#   1. 实体动力学 (EntityDynamics) — 被观测对象的变化速率 Q
#      · 不是信息在衰减，是实体状态本身在漂移
#      · Q 取决于实体类型，不取决于观测者
#   2. 传感器特征 (SensorCharacteristics) — 测量精度 R
#      · 观测噪声方差、系统偏差、校准状态
#   3. 贝叶斯融合 (bayesian_fuse) — 逆方差加权，不是二选一
#      · σ²_total = Q·Δt + R
#      · 后验 = Σ(x_i / σ²_i) / Σ(1 / σ²_i)
#      · 融合后的精度总是高于任一单源精度
#   4. 反馈学习 (update_sensor_reliability) — 用真实结果校准参数
#      · 预测误差 → 更新 R (观测噪声)
#      · 连续误报 → 降低该 sensor 的有效权重


# ══ 8.1 实体动力学 ══

# 预设实体类型（模块级常量）
ENTITY_PRESETS: dict[str, dict[str, Any]] = {
    "static": {
        "Q": 0.0,
        "desc": "静态实体（建筑位置、地理信息、历史档案）——状态几乎不随时间变化",
    },
    "slow": {
        "Q": 1e-10,
        "desc": "慢变实体（人口数据、供应链库存、用地规划）——天~周尺度变化",
    },
    "fast": {
        "Q": 1e-4,
        "desc": "快变实体（车流量、电网负载、温度）——秒~分尺度变化",
    },
    "ultra_fast": {
        "Q": 1.0,
        "desc": "超快实体（振动信号、股价、雷达回波）——毫秒~秒尺度变化",
    },
}


@dataclass
class EntityDynamics:
    """被观测实体的状态变化特征——控制信息随时间的不确定性增长。

    关键概念：衰减速率属于实体，不属于传感器。
    建筑位置是"静态"实体——R=GPS测量误差，Q≈0（建筑不移动）。
    股价是"超快"实体——即使完美测量(R=0)，1ms 后状态已完全不同。

    数学：
      σ²_process(t) = Q · Δt
      即：实体的不确定性随经过的时间线性增长。
    """

    entity_type: str  # "static" | "slow" | "fast" | "ultra_fast"
    process_noise_per_second: float  # Q — 单位时间的不确定性增长
    description: str = ""

    @classmethod
    def from_preset(cls, entity_type: str) -> EntityDynamics:
        """从预设类型创建。"""
        preset = ENTITY_PRESETS.get(entity_type, ENTITY_PRESETS["fast"])
        return cls(
            entity_type=entity_type,
            process_noise_per_second=preset["Q"],
            description=preset["desc"],
        )

    def process_noise_at_age(self, age_seconds: float) -> float:
        """经过 age_seconds 秒后积累的过程噪声 σ² = Q·Δt。"""
        return self.process_noise_per_second * age_seconds


# ══ 8.2 传感器特征 ══

# 预设传感器精度（模块级常量）
SENSOR_PRESETS: dict[str, dict[str, Any]] = {
    "precision": {"R": 0.01, "desc": "精密传感器——校准良好，噪声极小"},
    "standard": {"R": 1.0, "desc": "标准传感器——典型工业精度"},
    "coarse": {"R": 10.0, "desc": "粗精度——低分辨率或未校准"},
    "human": {"R": 50.0, "desc": "人工报告——主观判断，高方差"},
}


@dataclass
class SensorCharacteristics:
    """传感器/信息源的测量特征——描述观测本身的不确定性。

    数学：
      σ²_observation = R  （测量噪声方差）
      σ²_total = σ²_process(Q·Δt) + σ²_observation(R)

    R 小的传感器精度高——即使数据陈旧，仍有参考价值。
    R 大的传感器噪声大——新鲜数据也不完全可靠。
    """

    observation_variance: float  # R — 测量噪声方差
    bias: float = 0.0  # 系统偏差（校准后可修正）
    calibration_age_ms: int = 0  # 距上次校准的时间
    reliability_score: float = 1.0  # 历史可靠性 (0~1)，反馈学习更新

    @classmethod
    def from_preset(cls, preset_name: str) -> SensorCharacteristics:
        """从预设精度创建。"""
        preset = SENSOR_PRESETS.get(preset_name, SENSOR_PRESETS["standard"])
        return cls(observation_variance=preset["R"])

    @property
    def precision(self) -> float:
        """精度 = 1/R —— 方差的倒数，越大越好。"""
        if self.observation_variance <= 0:
            return float("inf")
        return 1.0 / self.observation_variance


# ══ 8.3 贝叶斯融合 ══


@dataclass
class FusedEstimate:
    """贝叶斯融合后的后验估计。

    核心思想：不选赢家——用逆方差加权把所有观测融合成一个后验分布。
    融合后的精度总是高于任一单源精度（这是数学保证，不是启发式）。

    数学：
      w_i = 1 / (Q·Δt_i + R_i)          ← 逆总方差 = 精度
      μ   = Σ(w_i · x_i) / Σ(w_i)       ← 后验均值（精度加权平均）
      σ²  = 1 / Σ(w_i)                  ← 后验方差
      CI  = μ ± 1.96σ                   ← 95% 置信区间
    """

    posterior_mean: float  # μ — 后验均值（最佳估计）
    posterior_variance: float  # σ² — 后验方差（越小越确定）
    confidence_95: tuple[float, float]  # 95% 置信区间
    source_count: int  # 融合了多少个信息源
    contributions: list[dict[str, Any]] = field(default_factory=list)
    # 每个信息源的贡献: {agent, value, total_variance, weight, weight_pct}

    @property
    def posterior_precision(self) -> float:
        """后验精度 = 1/σ²。"""
        if self.posterior_variance <= 0:
            return float("inf")
        return 1.0 / self.posterior_variance

    @property
    def is_reliable(self) -> bool:
        """后验是否可靠——95% CI 宽度是否合理。"""
        width = self.confidence_95[1] - self.confidence_95[0]
        return width < 5.0  # 阈值可调

    def summary(self) -> str:
        """人类可读的融合结果摘要。"""
        return (
            f"后验估计: {self.posterior_mean:.2f} ± {1.96 * (self.posterior_variance ** 0.5):.2f} "
            f"(95% CI: [{self.confidence_95[0]:.2f}, {self.confidence_95[1]:.2f}], "
            f"{self.source_count} 源融合)"
        )


def bayesian_fuse(
    observations: list[tuple[float, float, SensorCharacteristics]],
    entity: EntityDynamics,
    now_seconds: float | None = None,
) -> FusedEstimate:
    """贝叶斯融合——多源观测的精度加权平均。

    不是二选一。每个观测都有价值——即使陈旧、即使噪声大。

    Args:
        observations: [(观测值, 时间戳(Unix秒), 传感器特征), ...]
        entity: 被观测实体的动力学特征
        now_seconds: 当前时间（None=自动取当前时间）

    Returns:
        FusedEstimate — 后验分布

    Example:
        # 两个温度传感器报告同一车间温度
        entity = EntityDynamics.from_preset("fast")  # 温度是快变实体
        fast_sensor = SensorCharacteristics.from_preset("precision")  # R=0.01
        slow_sensor = SensorCharacteristics.from_preset("standard")   # R=1.0

        result = bayesian_fuse([
            (85.0, now - 5,    fast_sensor),   # 5秒前，精密传感器
            (75.0, now - 3600, slow_sensor),   # 1小时前，标准传感器
        ], entity)
        # → posterior_mean ≈ 84.9 (精密传感器权重远大于陈旧的标准传感器)
        # → posterior_variance < 0.01 (两个源融合后精度更高)
    """
    import math
    import time as _time

    if not observations:
        raise ValueError("至少需要一个观测")

    now = now_seconds or _time.time()

    # 1. 为每个观测计算总方差和精度权重
    weighted: list[dict[str, Any]] = []
    total_weight = 0.0

    for value, ts, sensor in observations:
        age_s = max(0.0, now - ts)
        process_var = entity.process_noise_at_age(age_s)  # Q·Δt
        total_var = process_var + sensor.observation_variance  # Q·Δt + R
        precision_w = 1.0 / total_var if total_var > 0 else float("inf")
        weight = precision_w * sensor.reliability_score  # 可靠性折扣

        weighted.append({
            "value": value,
            "age_s": age_s,
            "process_variance": process_var,
            "observation_variance": sensor.observation_variance,
            "total_variance": total_var,
            "precision": precision_w,
            "reliability": sensor.reliability_score,
            "weight": weight,
        })
        total_weight += weight

    if total_weight <= 0:
        raise ValueError("所有观测的权重总和为 0——传感器可能全部失效")

    # 2. 精度加权平均
    posterior_mean = sum(w["value"] * w["weight"] for w in weighted) / total_weight
    posterior_variance = 1.0 / total_weight  # σ² = 1/Σw

    # 3. 95% 置信区间
    sigma = math.sqrt(posterior_variance)
    ci_low = posterior_mean - 1.96 * sigma
    ci_high = posterior_mean + 1.96 * sigma

    # 4. 各源贡献分析
    contributions = []
    for w in weighted:
        contributions.append({
            "value": w["value"],
            "age_s": round(w["age_s"], 3),
            "total_variance": round(w["total_variance"], 6),
            "weight_pct": round(w["weight"] / total_weight * 100, 1),
        })

    return FusedEstimate(
        posterior_mean=posterior_mean,
        posterior_variance=posterior_variance,
        confidence_95=(ci_low, ci_high),
        source_count=len(observations),
        contributions=contributions,
    )


# ══ 8.4 反馈学习 ══


def update_sensor_reliability(
    sensor: SensorCharacteristics,
    observed_value: float,
    observed_timestamp_s: float,
    ground_truth: float,
    entity: EntityDynamics,
    *,
    now_s: float | None = None,
    learning_rate: float = 0.1,
) -> SensorCharacteristics:
    """用真实结果更新传感器的可靠性分数。

    每次获得 ground truth 后调用——系统从自己的错误中学习。

    关键：用传感器自身的总方差做归一化，不是融合后验。
    σ²_total = Q·Δt + R
    z = |x_observed - y_truth| / σ_total
    z 小 → 传感器工作正常 → 可靠性上升
    z 大 → 传感器可能故障/失准 → 可靠性下降

    Args:
        sensor: 要更新的传感器
        observed_value: 传感器当时报告的值
        observed_timestamp_s: 观测时间戳（Unix 秒）
        ground_truth: 后来确认的真实值
        entity: 被观测实体的动力学（用于计算 Q·Δt）
        now_s: 当前时间
        learning_rate: EMA 平滑系数

    Returns:
        更新后的 SensorCharacteristics
    """
    import time as _time

    now = now_s or _time.time()
    age_s = max(0.0, now - observed_timestamp_s)
    total_var = entity.process_noise_at_age(age_s) + sensor.observation_variance
    sigma = total_var ** 0.5

    if sigma <= 0:
        return sensor  # 完美传感器，无需更新

    z_score = abs(observed_value - ground_truth) / sigma

    # 连续调整：1σ 内加分，超过 1σ 连续扣分
    if z_score < 1.0:
        adjustment = +0.05  # 在预期范围内 → 加分
    else:
        # 偏离越大扣分越重（capped 防止单次灾难性降权）
        adjustment = -0.02 * min(z_score, 20.0)

    sensor.reliability_score = max(0.05, min(1.0,
        sensor.reliability_score + learning_rate * adjustment
    ))

    return sensor


# ══ 8.5 向后兼容 — 旧版 arbitrate() ══
# 保留旧接口，内部委托给 bayesian_fuse()。


@dataclass
class ArbitrationResult:
    """[已弃用] 旧版二选一仲裁结果。保留用于向后兼容。
    新代码应使用 bayesian_fuse() → FusedEstimate。"""

    winner: AgentRef
    confidence: float
    reasoning: str
    conflict: bool
    report: dict[str, Any] = field(default_factory=dict)


def arbitrate(
    reports: list[tuple[TrigramMessage, AgentRegistration]],
    entity_id: str = "",
) -> ArbitrationResult:
    """[已弃用] 二选一仲裁。保留向后兼容。
    新代码应使用 bayesian_fuse()。"""
    if not reports:
        raise ValueError("至少需要一条报告")

    import time as _time

    entity = EntityDynamics.from_preset("fast")  # 默认快变实体
    now_s = _time.time()

    obs_list: list[tuple[float, float, SensorCharacteristics]] = []
    agent_list: list[AgentRef] = []
    for msg, agent_reg in reports:
        # 从旧的 InfoDecayConfig 估算 R（粗略近似）
        half_life_s = agent_reg.time_scale.decay.half_life_ms / 1000.0
        r_approx = half_life_s * 0.1  # 衰减越快 → 测量越不精确
        sensor = SensorCharacteristics(
            observation_variance=r_approx,
            reliability_score=1.0,
        )
        # 从 payload 中尝试提取数值
        value = 0.0
        if msg.payload:
            for v in msg.payload.values():
                if isinstance(v, (int, float)):
                    value = float(v)
                    break
        obs_list.append((value, msg.timestamp / 1000.0, sensor))
        agent_list.append(agent_reg.ref)

    fused = bayesian_fuse(obs_list, entity, now_s)

    # 找贡献最大的源
    max_pct = max(c["weight_pct"] for c in fused.contributions)
    winner_idx = next(
        i for i, c in enumerate(fused.contributions)
        if c["weight_pct"] == max_pct
    )

    conflict = False
    if len(fused.contributions) >= 2:
        sorted_pct = sorted(c["weight_pct"] for c in fused.contributions)
        conflict = abs(sorted_pct[-1] - sorted_pct[-2]) < 15.0

    return ArbitrationResult(
        winner=agent_list[winner_idx],
        confidence=1.0 - fused.posterior_variance,
        reasoning=fused.summary(),
        conflict=conflict,
        report={
            "entity": entity_id,
            "posterior_mean": fused.posterior_mean,
            "posterior_variance": fused.posterior_variance,
            "confidence_95": fused.confidence_95,
            "sources": fused.contributions,
            "conflict": conflict,
        },
    )
