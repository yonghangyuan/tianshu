"""RAG Service — 文档摄取 / 混合检索 / 知识库管理。

区别于对话记忆 (memory/: FTS5 + Digest + Decay):
RAG 面向本地文档库（PDF/Markdown/代码），向量 + BM25 混合检索。

用法:
    svc = RAGService(config)
    await svc.ingest_path("docs/", collection="default")
    hits = await svc.search("什么是三爻架构", collection="default")
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .chunker import chunk_text
from .embedder import BaseEmbedder, create_embedder
from .store import HybridStore

SUPPORTED_EXTS = {
    ".md", ".markdown", ".txt", ".py", ".json", ".yaml", ".yml",
    ".csv", ".html", ".htm", ".log", ".rst", ".pdf",
}
_PDF_EXTS = {".pdf"}

DEFAULT_CONFIG: dict[str, Any] = {
    "embedding": {"provider": "mock", "dims": 256},
    "storage": {"dir": "~/.tianshu/rag"},
    "chunking": {"size": 800, "overlap": 120},
    "search": {"top_k": 5, "vector_weight": 0.6},
}


def _deep_update(base: dict, override: dict) -> None:
    """递归合并配置（override 覆盖 base）。"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


_service: RAGService | None = None


def get_service() -> RAGService:
    """进程级单例 — 首次调用时加载 config/rag.yaml（项目级 + 用户级）。"""
    global _service
    if _service is None:
        from tianshu.core.config import load_rag_config
        # 开发模式: 定位项目级 config/rag.yaml；安装模式: 仅用户级配置 + 默认值
        project_cfg = Path(__file__).resolve().parents[3] / "config" / "rag.yaml"
        config = load_rag_config(str(project_cfg)) if project_cfg.exists() else {}
        _service = RAGService(config)
    return _service


class RAGService:
    """RAG 服务入口。

    未配置 embedding API Key 时自动降级为 MockEmbedder（offline 模式），
    摄取/检索链路仍完整可用，检索质量依赖关键词。
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = DEFAULT_CONFIG
        _deep_update(self.config, config or {})
        self.embedder, self.offline = create_embedder(self.config)

        storage = self.config.get("storage", {})
        db_path = Path(storage.get("dir", "~/.tianshu/rag")).expanduser() / "rag.db"
        self.store = HybridStore(db_path)

        chunking = self.config.get("chunking", {})
        self._chunk_size = int(chunking.get("size", 800))
        self._overlap = int(chunking.get("overlap", 120))

    @property
    def embedder_name(self) -> str:
        return self.embedder.name

    # ── 摄取 ──────────────────────────────────────────────────────────

    async def ingest_text(self, text: str, source: str = "",
                          collection: str = "default",
                          title: str = "") -> dict[str, Any]:
        """摄取一段文本。返回 {chunks, added}。"""
        chunks = chunk_text(text, source=source, size=self._chunk_size,
                            overlap=self._overlap)
        if title:
            for c in chunks:
                c.title = c.title or title
        if not chunks:
            return {"chunks": 0, "added": 0}
        vectors = await self.embedder.embed([c.text for c in chunks])
        items = [
            {"chunk_id": c.chunk_id, "text": c.text, "source": c.source,
             "title": c.title, "vector": v}
            for c, v in zip(chunks, vectors)
        ]
        added = await self.store.add(collection, items)
        return {"chunks": len(chunks), "added": added}

    async def ingest_path(self, path: str | Path,
                          collection: str = "default") -> dict[str, Any]:
        """摄取文件或目录（递归）。返回 {files, chunks, added, skipped}。"""
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"路径不存在: {path}")
        if p.is_file():
            files = [p]
        else:
            files = sorted(
                f for f in p.rglob("*")
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS
            )
        result: dict[str, Any] = {"files": 0, "chunks": 0, "added": 0, "skipped": []}
        for f in files:
            try:
                text = await asyncio.to_thread(self._read_file, f)
                if not text or not text.strip():
                    result["skipped"].append(f"{f} (空文件)")
                    continue
                r = await self.ingest_text(text, source=str(f), collection=collection)
                result["files"] += 1
                result["chunks"] += r["chunks"]
                result["added"] += r["added"]
            except Exception as e:  # 单文件失败不阻塞整体
                result["skipped"].append(f"{f} ({e})")
        return result

    def _read_file(self, f: Path) -> str:
        if f.suffix.lower() in _PDF_EXTS:
            try:
                from pypdf import PdfReader
            except ImportError as e:
                raise RuntimeError("PDF 摄取需要 pypdf: pip install 'tianshu[rag]'") from e
            reader = PdfReader(str(f))
            return "\n\n".join((page.extract_text() or "") for page in reader.pages)
        return f.read_text(encoding="utf-8", errors="replace")

    # ── 检索 ──────────────────────────────────────────────────────────

    async def search(self, query: str, collection: str = "default",
                     top_k: int = 0) -> list[dict[str, Any]]:
        """混合检索：向量 + BM25 → RRF 融合。

        Returns:
            [{chunk_id, text, source, title, score, rank, fusion}]
        """
        if not query.strip():
            return []
        search_cfg = self.config.get("search", {})
        k = top_k or int(search_cfg.get("top_k", 5))
        vector_weight = float(search_cfg.get("vector_weight", 0.6))
        vector = (await self.embedder.embed([query]))[0]
        return await self.store.search_hybrid(
            collection, query, vector, k=k, vector_weight=vector_weight,
        )

    # ── 集合管理 ──────────────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """知识库状态: embedding 模式 + 集合统计。"""
        return {
            "offline": self.offline,
            "embedder": self.embedder_name,
            "db": str(self.store._db_path),
            "collections": await self.store.collections(),
        }

    async def delete_collection(self, collection: str) -> int:
        """删除集合及全部索引，返回删除的 chunk 数。"""
        return await self.store.delete_collection(collection)
