"""RAG 私有知识库 Skill — 文档摄取、向量索引、混合检索。

三爻分类: 地（感知/检索）
"""

from __future__ import annotations

from tianshu.rag.service import get_service

from .base import BaseSkill, SkillTool


def _fmt_hits(results: list[dict]) -> str:
    if not results:
        return "未找到相关内容"
    lines = []
    for r in results:
        src = f" [{r.get('source', '')}]" if r.get("source") else ""
        lines.append(
            f"#{r.get('rank', '?')} (score={r.get('score', 0.0):.3f}){src}\n"
            f"{r.get('text', '')[:500]}"
        )
    return "\n\n".join(lines)


async def _ingest(path: str, collection: str = "default") -> str:
    svc = get_service()
    r = await svc.ingest_path(path, collection)
    parts = [f"摄取完成: {r['files']} 文件, {r['chunks']} chunks, 新增 {r['added']}"]
    if r["skipped"]:
        shown = ", ".join(str(s) for s in r["skipped"][:5])
        parts.append(f"跳过 {len(r['skipped'])}: {shown}")
    return "\n".join(parts)


async def _search(query: str, collection: str = "default", top_k: int = 5) -> str:
    svc = get_service()
    results = await svc.search(query, collection, top_k)
    return _fmt_hits(results)


async def _status() -> str:
    svc = get_service()
    st = await svc.status()
    mode = "离线 Mock (未配置 API Key)" if st["offline"] else f"API ({st['embedder']})"
    lines = [f"RAG 知识库 · embedding: {mode}", f"存储: {st['db']}"]
    cols = st["collections"]
    if not cols:
        lines.append("暂无集合 — 用 rag_ingest 摄取文档")
    for c in cols:
        lines.append(f"- {c['name']}: {c['chunks']} chunks / {c['sources']} 来源")
    return "\n".join(lines)


async def _delete(collection: str) -> str:
    svc = get_service()
    n = await svc.delete_collection(collection)
    return f"已删除集合 '{collection}': {n} chunks"


class RAGSkill(BaseSkill):
    name = "rag"
    description = "私有知识库: 摄取本地文档(PDF/Markdown/代码)建混合索引，语义+关键词检索"
    trigram = "地"
    trigger_keywords = [
        "知识库", "rag", "私有文档", "文档检索", "语义搜索",
        "摄取文档", "建索引", "检索文档",
    ]

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="rag_ingest",
                description=(
                    "把本地文件/目录摄取进 RAG 私有知识库，建立向量+BM25混合索引。"
                    "支持 .md/.txt/.py/.json/.yaml/.csv/.html/.pdf。重复摄取自动去重。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件或目录路径"},
                        "collection": {"type": "string", "description": "集合名，默认 default"},
                    },
                    "required": ["path"],
                },
                handler=_ingest,
                permission_level=2,
            ),
            SkillTool(
                name="rag_search",
                description=(
                    "在 RAG 私有知识库中混合检索（语义向量+BM25关键词，RRF融合），"
                    "返回最相关片段。检索不到时考虑先 rag_ingest 摄取文档。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索查询"},
                        "collection": {"type": "string", "description": "集合名，默认 default"},
                        "top_k": {"type": "integer", "description": "返回条数，默认 5"},
                    },
                    "required": ["query"],
                },
                handler=_search,
                permission_level=0,
            ),
            SkillTool(
                name="rag_status",
                description="查看 RAG 知识库状态：embedding 模式、集合、chunk 数、来源数。",
                parameters={"type": "object", "properties": {}},
                handler=_status,
                permission_level=0,
            ),
            SkillTool(
                name="rag_delete",
                description="删除一个知识库集合及其全部索引。",
                parameters={
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "要删除的集合名"},
                    },
                    "required": ["collection"],
                },
                handler=_delete,
                permission_level=2,
            ),
        ]
