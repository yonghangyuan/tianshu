"""决策溯源数据结构。

从 demo_audit_system.py 迁移，统一为天爻可追溯的数据模型。

一条完整的决策溯源链：
  ProvenanceInput → DecisionProvenance → ProvenanceCommand → ProvenanceEvaluation
      (输入快照)        (LLM决策记录)        (输出命令)         (事后评估)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProvenanceInput:
    """决策输入——LLM 做出决策时"看到了什么"。

    借鉴 demo_audit_system.py ProvenanceInput。
    """
    data_source: str          # "file_system" | "arxiv_api" | "git_repo" | "user_input"
    source_id: str            # 数据源唯一标识
    content_hash: str         # SHA256 前 12 位
    confidence: float = 1.0   # 数据置信度
    latency: float = 0.0      # 数据延迟（秒）
    path: list[str] = field(default_factory=list)  # 数据流转路径
    timestamp_observed: float = 0.0

    @staticmethod
    def from_content(source: str, source_id: str, content: str, **kwargs: Any) -> ProvenanceInput:
        """从内容自动计算 hash。"""
        h = hashlib.sha256(content.encode()).hexdigest()[:12]
        return ProvenanceInput(data_source=source, source_id=source_id, content_hash=h, **kwargs)


@dataclass
class ProvenanceCommand:
    """决策输出——LLM 决定"做什么"。"""
    command_type: str         # "search_papers" | "download_pdf" | "classify" | "write_file"
    target: str               # 操作目标
    payload: dict[str, Any] = field(default_factory=dict)
    comm_path: list[str] = field(default_factory=list)  # 命令下发路径
    expected_outcome: str = ""


@dataclass
class ProvenanceEvaluation:
    """事后评估——决策的"实际效果如何"。"""
    actual_outcome: str
    discrepancy: list[str] = field(default_factory=list)     # 预期 vs 实际的差异
    root_cause: str = ""                                      # 根因分析
    feedback_action: str = ""                                 # 改进措施


@dataclass
class DecisionProvenance:
    """完整的决策溯源记录。

    借鉴 demo_audit_system.py DecisionProvenance。
    """
    decision_id: str
    timestamp: float
    llm_model: str
    llm_config: dict[str, Any] = field(default_factory=dict)
    system_prompt_version: str = ""
    available_tools: list[str] = field(default_factory=list)

    input_data: list[ProvenanceInput] = field(default_factory=list)
    output_commands: list[Any] = field(default_factory=list)  # ProvenanceCommand-like
    reasoning_chain: list[str] = field(default_factory=list)

    evaluation: ProvenanceEvaluation | None = None

    level: int = 1  # 1=ID+日志, 2=+快照, 3=+推理, 4=+评估
    session_id: str = ""
    task_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON（写入 SQLite）。"""
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "llm_model": self.llm_model,
            "llm_config": json.dumps(self.llm_config),
            "system_prompt_version": self.system_prompt_version,
            "available_tools": self.available_tools,
            "input_count": len(self.input_data),
            "input_data": json.dumps(
                [{"source": i.data_source, "id": i.source_id,
                  "hash": i.content_hash, "confidence": i.confidence}
                 for i in self.input_data],
                ensure_ascii=False,
            ),
            "reasoning_chain": json.dumps(self.reasoning_chain, ensure_ascii=False),
            "output_commands": json.dumps(
                [{"type": c.command_type, "target": c.target, "payload": c.payload}
                 for c in self.output_commands],
                ensure_ascii=False,
            ),
            "evaluation": json.dumps(
                {
                    "actual_outcome": self.evaluation.actual_outcome,
                    "discrepancy": self.evaluation.discrepancy,
                    "root_cause": self.evaluation.root_cause,
                    "feedback": self.evaluation.feedback_action,
                }
            ) if self.evaluation else None,
            "level": self.level,
            "session_id": self.session_id,
            "task_type": self.task_type,
        }
