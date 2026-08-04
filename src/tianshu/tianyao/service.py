"""Audit Service — 四级决策审计的独立服务封装。

当前后端: SQLite
未来可换: Postgres / ClickHouse
"""

from __future__ import annotations

import time
from typing import Any

from ..sdk.models import AuditRecord, ProvenanceEvaluation
from .audit import AuditStore, AuditRecorder


class AuditService:
    """审计服务——封装存储+记录，对外暴露查询 API。"""

    def __init__(self, db_path: str = "tianshu.db") -> None:
        self._store = AuditStore(db_path)
        self._recorder = AuditRecorder(self._store)

    # ── 写入 ───────────────────────────────────────────────────────

    def generate_id(self) -> str:
        return self._recorder.generate_id()

    def capture_snapshot(self) -> list[Any]:
        return self._recorder.capture_snapshot()

    async def record(self, record: AuditRecord) -> None:
        """异步写入审计记录。"""
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
        self._recorder.record(p)

    async def evaluate(self, decision_id: str, evaluation: ProvenanceEvaluation) -> None:
        """回填事后评估。"""
        from .provenance import ProvenanceEvaluation as PE
        pe = PE(
            actual_outcome=evaluation.actual_outcome,
            discrepancy=evaluation.discrepancy,
            root_cause=evaluation.root_cause,
            feedback_action=evaluation.feedback_action,
        )
        await self._store.update_evaluation(decision_id, pe)

    # ── 查询 ──────────────────────────────────────────────────────

    async def query(self, decision_id: str) -> dict | None:
        r = await self._store.get(decision_id)
        return r.to_dict() if r else None

    async def recent(self, limit: int = 10) -> list[dict]:
        records = await self._store.list_recent(limit)
        return [r.to_dict() for r in records]

    async def count(self) -> int:
        return await self._store.count()

    async def store_snapshot(self, decision_id: str, data: dict) -> None:
        """存储轻量级快照——压缩记录、中间状态等。"""
        import json, aiosqlite
        async with aiosqlite.connect(self._store._db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO audit_snapshots (decision_id, data, created_at)
                   VALUES (?, ?, ?)""",
                (decision_id, json.dumps(data, ensure_ascii=False), time.time()),
            )
            await db.commit()

    async def get_snapshot(self, decision_id: str) -> dict | None:
        """查询快照。表不存在时优雅降级。"""
        import json, aiosqlite
        try:
            async with aiosqlite.connect(self._store._db_path) as db:
                cursor = await db.execute(
                    "SELECT data FROM audit_snapshots WHERE decision_id = ?",
                    (decision_id,),
                )
                row = await cursor.fetchone()
            return json.loads(row[0]) if row else None
        except Exception:
            return None
