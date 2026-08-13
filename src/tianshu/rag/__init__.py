"""RAG 私有知识库 — 文档摄取、向量索引、混合检索。

区别于对话记忆 (memory/: FTS5 + Digest + Decay):
RAG 面向本地文档库 (PDF/Markdown/代码)，向量 + BM25 混合检索。

模块:
  chunker   — Markdown 感知分块器
  embedder  — Embedding Provider (OpenAI 兼容 API + 离线 Mock)
  store     — HybridStore: SQLite FTS5 (BM25) + float32 向量 + RRF 融合
  service   — RAGService: 摄取/检索/集合管理
"""

from .service import RAGService
from .chunker import Chunk, chunk_text
from .embedder import BaseEmbedder, MockEmbedder, OpenAICompatEmbedder, create_embedder
from .store import HybridStore

__all__ = [
    "RAGService",
    "Chunk", "chunk_text",
    "BaseEmbedder", "MockEmbedder", "OpenAICompatEmbedder", "create_embedder",
    "HybridStore",
]
