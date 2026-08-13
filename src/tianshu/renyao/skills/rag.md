---
name: rag
description: 私有知识库 — 文档摄取 + 混合检索
trigram: 地
permission: SAFE
tools: [rag_ingest, rag_search, rag_status, rag_delete]
trigger_keywords: [知识库, rag, 私有文档, 文档检索, 语义搜索]
---
# RAG Skill
私有知识库：把本地文档（PDF/Markdown/代码）摄取进 SQLite 混合索引，
向量（embedding API）+ BM25（FTS5）RRF 融合检索。

## 架构
- 分块: Markdown 标题感知 + 段落贪心 + 滑动窗口（重叠）
- Embedding: OpenAI 兼容 /embeddings（智谱/豆包/硅基流动），未配 Key 降级离线 Mock
- 存储: SQLite FTS5 (BM25) + float32 向量 BLOB，零新增硬依赖
- 检索: 向量余弦 + BM25 → RRF 融合排序

## 配置
`config/rag.yaml` — embedding/storage/chunking/search 四段。
用户级覆盖: `~/.tianshu/rag.yaml`。

## 与对话记忆的区别
对话记忆 (memory/) 是 FTS5 + Digest + Decay 的短期事实层；
RAG 是文档库的语义索引层，两者互补。
