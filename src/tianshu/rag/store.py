"""混合向量库 — SQLite FTS5 (BM25) + float32 向量 (余弦) + RRF 融合。

零新增硬依赖: FTS5 为 SQLite 内置，向量用 struct 打包 float32 BLOB。
numpy 可用时自动加速余弦计算；不可用时纯 Python 回退。

中文处理: FTS5 unicode61 会把整段汉字当成一个 token，
因此索引/查询前先把 CJK 串切为 2-gram 空格分隔（_seg_cjk）。
查询侧 grams 用 OR 连接——跨词边界的 gram 在文档中不存在，AND 会漏检。

去重键: (collection, chunk_id) 复合唯一——同一文档可进入多个集合。
"""

from __future__ import annotations

import math
import re
import struct
import time
from pathlib import Path
from typing import Any

import aiosqlite

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
    id UNINDEXED,
    text,
    source UNINDEXED,
    title UNINDEXED,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    vector BLOB NOT NULL,
    dims INTEGER NOT NULL,
    created REAL NOT NULL,
    UNIQUE(collection, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_rag_collection ON rag_chunks(collection);
"""

_RRF_K = 60
_CJK_RE = re.compile(r"([一-鿿]+)")


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes, dims: int) -> list[float]:
    return list(struct.unpack(f"{dims}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def _seg_cjk(text: str) -> str:
    """CJK 连续串 → 2-gram 空格分隔；单字保持原样。英文/数字不受影响。"""
    def _grams(run: str) -> str:
        if len(run) == 1:
            return run
        return " ".join(run[i:i + 2] for i in range(len(run) - 1))

    return _CJK_RE.sub(lambda m: _grams(m.group(1)), text)


def _fts_query(query: str) -> str:
    """用户查询 → 安全 FTS5 查询串（token 加引号 OR 连接）。

    OR 而非 AND: 2-gram 查询串中跨词边界的 gram 在文档中不存在，
    AND 会全部漏检；BM25 排序自然把多命中的文档排前。
    """
    tokens = re.findall(r"[a-zA-Z0-9_]+|[一-鿿]{2}", _seg_cjk(query).lower())
    return " OR ".join(f'"{t}"' for t in tokens)


def _row_to_hit(row: tuple, rank: int) -> dict[str, Any]:
    return {
        "chunk_id": row[0], "text": row[1], "source": row[2],
        "title": row[3], "score": float(row[4] if len(row) > 4 else 0.0),
        "rank": rank,
    }


class HybridStore:
    """SQLite 混合存储: FTS5 关键词 + float32 向量，RRF 融合检索。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    async def _init(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        self._initialized = True

    # ── 写入 ──────────────────────────────────────────────────────────

    async def add(self, collection: str, items: list[dict[str, Any]]) -> int:
        """批量写入 chunk。items: {chunk_id, text, source, title, vector}

        (collection, chunk_id) 已存在则跳过（幂等摄入）。返回新增数。
        """
        if not items:
            return 0
        await self._init()
        now = time.time()
        added = 0
        async with aiosqlite.connect(str(self._db_path)) as db:
            for it in items:
                vec = it["vector"]
                dims = len(vec)
                cur = await db.execute(
                    "SELECT id, dims FROM rag_chunks WHERE collection=? AND chunk_id=?",
                    (collection, it["chunk_id"]),
                )
                row = await cur.fetchone()
                if row is not None:
                    if row[1] == dims:
                        continue
                    # 同 chunk 不同维度（embedding 配置变更）→ 删除重建
                    await db.execute("DELETE FROM rag_chunks WHERE id=?", (row[0],))
                    await db.execute("DELETE FROM rag_chunks_fts WHERE id=?", (row[0],))
                text = it["text"]
                cur = await db.execute(
                    "INSERT INTO rag_chunks"
                    " (collection, chunk_id, text, source, title, vector, dims, created)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (collection, it["chunk_id"], text, it.get("source", ""),
                     it.get("title", ""), _pack(vec), dims, now),
                )
                await db.execute(
                    "INSERT INTO rag_chunks_fts (id, text, source, title) VALUES (?,?,?,?)",
                    (cur.lastrowid, _seg_cjk(text), it.get("source", ""), it.get("title", "")),
                )
                added += 1
            await db.commit()
        return added

    # ── 检索 ──────────────────────────────────────────────────────────

    async def search_keyword(self, collection: str, query: str, k: int = 5) -> list[dict[str, Any]]:
        """FTS5 BM25 关键词检索。"""
        await self._init()
        q = _fts_query(query)
        if not q:
            return []
        async with aiosqlite.connect(str(self._db_path)) as db:
            cur = await db.execute(
                "SELECT r.chunk_id, r.text, r.source, r.title, bm25(rag_chunks_fts)"
                " FROM rag_chunks_fts"
                " JOIN rag_chunks r ON r.id = rag_chunks_fts.id"
                " WHERE rag_chunks_fts MATCH ? AND r.collection = ?"
                " ORDER BY bm25(rag_chunks_fts) LIMIT ?",
                (q, collection, k),
            )
            rows = await cur.fetchall()
        return [_row_to_hit(r, i + 1) for i, r in enumerate(rows)]

    async def search_vector(self, collection: str, vector: list[float],
                            k: int = 5) -> list[dict[str, Any]]:
        """余弦相似度 top-k。维度不匹配的 chunk 自动跳过（embedding 配置变更安全）。"""
        await self._init()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cur = await db.execute(
                "SELECT chunk_id, text, source, title, vector, dims"
                " FROM rag_chunks WHERE collection=?",
                (collection,),
            )
            rows = await cur.fetchall()
        rows = [r for r in rows if r[5] == len(vector)]
        if not rows:
            return []

        scores: list[float]
        if _HAS_NUMPY:
            matrix = np.vstack([np.frombuffer(r[4], dtype=np.float32) for r in rows])
            q = np.asarray(vector, dtype=np.float32)
            denom = np.linalg.norm(matrix, axis=1) * np.linalg.norm(q) + 1e-9
            scores = ((matrix @ q) / denom).tolist()
        else:
            scores = [_cosine(vector, _unpack(r[4], r[5])) for r in rows]

        order = sorted(range(len(rows)), key=lambda i: -scores[i])[:k]
        return [
            {"chunk_id": rows[i][0], "text": rows[i][1], "source": rows[i][2],
             "title": rows[i][3], "score": scores[i], "rank": n + 1}
            for n, i in enumerate(order)
        ]

    async def search_hybrid(self, collection: str, query: str, vector: list[float],
                            k: int = 5, vector_weight: float = 0.6) -> list[dict[str, Any]]:
        """混合检索: 向量 + BM25 → RRF 融合打分。"""
        pool = max(k, 20)
        vec_results = await self.search_vector(collection, vector, k=pool)
        kw_results = await self.search_keyword(collection, query, k=pool)
        if not vec_results:
            return kw_results[:k]
        if not kw_results:
            return vec_results[:k]

        vec_rank = {r["chunk_id"]: i for i, r in enumerate(vec_results)}
        kw_rank = {r["chunk_id"]: i for i, r in enumerate(kw_results)}
        merged: dict[str, dict[str, Any]] = {}
        for r in vec_results:
            r["fusion"] = vector_weight / (_RRF_K + vec_rank[r["chunk_id"]] + 1)
            merged[r["chunk_id"]] = r
        for r in kw_results:
            m = merged.get(r["chunk_id"])
            if m is None:
                r["fusion"] = 0.0
                merged[r["chunk_id"]] = r
                m = r
            m["fusion"] += (1.0 - vector_weight) / (_RRF_K + kw_rank[r["chunk_id"]] + 1)

        ranked = sorted(merged.values(), key=lambda x: -x["fusion"])[:k]
        for i, r in enumerate(ranked):
            r["rank"] = i + 1
        return ranked

    # ── 集合管理 ──────────────────────────────────────────────────────

    async def collections(self) -> list[dict[str, Any]]:
        """列出所有集合: {name, chunks, sources}。"""
        await self._init()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cur = await db.execute(
                "SELECT collection, COUNT(*), COUNT(DISTINCT source)"
                " FROM rag_chunks GROUP BY collection ORDER BY collection"
            )
            rows = await cur.fetchall()
        return [{"name": r[0], "chunks": r[1], "sources": r[2]} for r in rows]

    async def chunk_count(self, collection: str) -> int:
        await self._init()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM rag_chunks WHERE collection=?", (collection,)
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def delete_collection(self, collection: str) -> int:
        """删除集合及全部索引，返回删除的 chunk 数。"""
        await self._init()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cur = await db.execute(
                "SELECT id FROM rag_chunks WHERE collection=?", (collection,)
            )
            ids = [r[0] for r in await cur.fetchall()]
            for row_id in ids:
                await db.execute("DELETE FROM rag_chunks_fts WHERE id=?", (row_id,))
            await db.execute("DELETE FROM rag_chunks WHERE collection=?", (collection,))
            await db.commit()
        return len(ids)
