"""天爻审计系统 — 四级决策审计 + SQLite 持久化。

从 demo_audit_system.py 迁移，核心变更：
  - 存储：内存 dict → SQLite（支持跨会话检索 + 因果链查询）
  - 快照采集：从模拟传感器 → 真实文件系统/API 状态采集
  - 评估回填：从单场景弹道评估 → 通用结果对比框架

四级审计：
  Level 1 — ID + 时间戳 + 模型标识（所有决策）
  Level 2 — + 世界状态快照（涉及 I/O 的决策）
  Level 3 — + 完整推理链（深度推理任务）
  Level 4 — + 事后评估回填（操作完成后异步评估）
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .provenance import (
    DecisionProvenance,
    ProvenanceEvaluation,
    ProvenanceInput,
)

# ── SQLite Schema ──────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_records (
    decision_id TEXT PRIMARY KEY,
    timestamp REAL NOT NULL,
    llm_model TEXT NOT NULL,
    llm_config TEXT NOT NULL DEFAULT '{}',
    system_prompt_version TEXT NOT NULL DEFAULT '',
    available_tools TEXT NOT NULL DEFAULT '[]',
    input_count INTEGER NOT NULL DEFAULT 0,
    input_data TEXT NOT NULL DEFAULT '[]',
    reasoning_chain TEXT NOT NULL DEFAULT '[]',
    output_commands TEXT NOT NULL DEFAULT '[]',
    evaluation TEXT,
    level INTEGER NOT NULL DEFAULT 1,
    session_id TEXT NOT NULL DEFAULT '',
    task_type TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS causal_chain (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'triggers',
    timestamp REAL NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES audit_records(decision_id),
    FOREIGN KEY (child_id) REFERENCES audit_records(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_records(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_records(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_level ON audit_records(level);
CREATE INDEX IF NOT EXISTS idx_causal_parent ON causal_chain(parent_id);
CREATE INDEX IF NOT EXISTS idx_causal_child ON causal_chain(child_id);
"""


# ── 审计存储器（SQLite） ──────────────────────────────────────────────────

class AuditStore:
    """SQLite 审计存储。"""

    def __init__(self, db_path: str = "tianshu.db") -> None:
        self._db_path = db_path
        self._initialized = False

    async def _init(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        self._initialized = True

    async def save(self, record: DecisionProvenance) -> None:
        """保存一条审计记录。"""
        await self._init()
        data = record.to_dict()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO audit_records
                   (decision_id, timestamp, llm_model, llm_config,
                    system_prompt_version, available_tools,
                    input_count, input_data, reasoning_chain,
                    output_commands, evaluation, level, session_id, task_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["decision_id"], data["timestamp"], data["llm_model"],
                    data["llm_config"], data["system_prompt_version"],
                    json.dumps(data["available_tools"]),
                    data["input_count"], data["input_data"],
                    data["reasoning_chain"], data["output_commands"],
                    data["evaluation"], data["level"],
                    data["session_id"], data["task_type"],
                ),
            )
            await db.commit()

    async def get(self, decision_id: str) -> DecisionProvenance | None:
        """按 ID 查询审计记录。"""
        await self._init()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM audit_records WHERE decision_id = ?",
                (decision_id,),
            )
            row = await cursor.fetchone()
            return self._row_to_record(row) if row else None

    async def list_recent(self, limit: int = 10) -> list[DecisionProvenance]:
        """列出最近 N 条记录。"""
        await self._init()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM audit_records ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_record(r) for r in rows]

    async def list_by_session(self, session_id: str) -> list[DecisionProvenance]:
        """按会话 ID 列出所有记录。"""
        await self._init()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM audit_records WHERE session_id = ? ORDER BY timestamp ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_record(r) for r in rows]

    async def update_evaluation(
        self, decision_id: str, evaluation: ProvenanceEvaluation
    ) -> None:
        """回填事后评估，升级到 Level 4。"""
        await self._init()
        eval_json = json.dumps({
            "actual_outcome": evaluation.actual_outcome,
            "discrepancy": evaluation.discrepancy,
            "root_cause": evaluation.root_cause,
            "feedback": evaluation.feedback_action,
        }, ensure_ascii=False)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE audit_records SET evaluation = ?, level = 4 WHERE decision_id = ?",
                (eval_json, decision_id),
            )
            await db.commit()

    async def link_causal(
        self, parent_id: str, child_id: str, relation: str = "triggers"
    ) -> None:
        """建立因果链：parent → child。"""
        await self._init()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO causal_chain (parent_id, child_id, relation, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (parent_id, child_id, relation, time.time()),
            )
            await db.commit()

    async def count(self) -> int:
        """总记录数。"""
        await self._init()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM audit_records")
            row = await cursor.fetchone()
            return row[0] if row else 0

    # ── 内部 ──

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> DecisionProvenance:
        """SQLite Row → DecisionProvenance。"""
        eval_data = None
        if row["evaluation"]:
            try:
                e = json.loads(row["evaluation"])
                eval_data = ProvenanceEvaluation(
                    actual_outcome=e.get("actual_outcome", ""),
                    discrepancy=e.get("discrepancy", []),
                    root_cause=e.get("root_cause", ""),
                    feedback_action=e.get("feedback", ""),
                )
            except json.JSONDecodeError:
                pass

        input_data = []
        try:
            for i in json.loads(row["input_data"]):
                input_data.append(ProvenanceInput(
                    data_source=i.get("source", ""),
                    source_id=i.get("id", ""),
                    content_hash=i.get("hash", ""),
                    confidence=i.get("confidence", 1.0),
                ))
        except json.JSONDecodeError:
            pass

        return DecisionProvenance(
            decision_id=row["decision_id"],
            timestamp=row["timestamp"],
            llm_model=row["llm_model"],
            input_data=input_data,
            reasoning_chain=json.loads(row["reasoning_chain"]),
            level=row["level"],
            session_id=row["session_id"],
            task_type=row["task_type"],
            evaluation=eval_data,
        )


# ── 审计记录器 ────────────────────────────────────────────────────────────

class AuditRecorder:
    """审计记录工厂。

    借鉴 demo_audit_system.py AuditRecorder：
      - generate_id()  → 分配决策 ID
      - capture_snapshot() → 采集世界状态快照
      - record()       → 存储审计记录
    """

    def __init__(self, store: AuditStore) -> None:
        self.store = store
        self._decision_counter = 0
        self._start_time = time.time()

    def generate_id(self) -> str:
        """分配唯一决策 ID。格式: D{序号}"""
        self._decision_counter += 1
        return f"D{self._decision_counter:06d}"

    def capture_snapshot(self) -> list[ProvenanceInput]:
        """采集当前世界状态快照。

        MVP 版本采集：
          - 当前工作目录及文件列表
          - 环境变量（脱敏）
        """
        inputs: list[ProvenanceInput] = []

        # 文件系统快照
        try:
            cwd = os.getcwd()
            files = os.listdir(cwd)[:50]  # 最多 50 个
            inputs.append(ProvenanceInput.from_content(
                source="filesystem",
                source_id=cwd,
                content="\n".join(files),
                confidence=1.0,
                path=[cwd],
            ))
        except Exception:
            pass

        # 环境快照（脱敏：不包含 API Key 值）
        try:
            safe_env = {
                k: "***" if any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "PASS"))
                else str(v)[:50]
                for k, v in os.environ.items()
                if not k.startswith("_")
            }
            inputs.append(ProvenanceInput.from_content(
                source="environment",
                source_id="os.environ",
                content=json.dumps(safe_env),
                confidence=1.0,
            ))
        except Exception:
            pass

        return inputs

    def record_direct(self, record) -> None:
        """直接记录审计（从 SDK AuditRecord 转换）。"""
        from .provenance import DecisionProvenance
        p = DecisionProvenance(
            decision_id=record.decision_id,
            timestamp=record.timestamp,
            llm_model=record.llm_model,
            llm_config=record.llm_config,
            system_prompt_version=record.system_prompt_version,
            available_tools=record.available_tools,
            input_data=record.input_data,
            output_commands=record.output_commands,
            reasoning_chain=record.reasoning_chain,
            level=record.level,
            session_id=record.session_id,
            task_type=record.task_type,
        )
        self.record(p)

    def record(self, provenance: DecisionProvenance) -> None:
        """记录审计（同步触发异步写入）。

        借鉴 demo_audit_system.py AuditRecorder.record()。
        在 Agent Loop 中被同步调用，异步写入 SQLite。
        """
        asyncio.create_task(self._async_record(provenance))

    async def _async_record(self, provenance: DecisionProvenance) -> None:
        try:
            await self.store.save(provenance)
        except Exception as e:
            print(f"  [天爻] 审计记录写入失败: {e}")

    async def record_success(self, decision_id: str, result: Any) -> None:
        """记录成功结果。"""
        evaluation = ProvenanceEvaluation(
            actual_outcome=str(result)[:500],
            discrepancy=[],
            root_cause="",
            feedback_action="",
        )
        await self.store.update_evaluation(decision_id, evaluation)

    async def record_failure(self, decision_id: str, error: Exception) -> None:
        """记录失败结果。"""
        evaluation = ProvenanceEvaluation(
            actual_outcome=f"失败: {error}",
            discrepancy=["操作未完成"],
            root_cause=str(error)[:500],
            feedback_action="检查输入参数和网络连接",
        )
        await self.store.update_evaluation(decision_id, evaluation)
