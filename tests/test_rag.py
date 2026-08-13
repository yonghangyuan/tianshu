"""RAG 私有知识库测试 — chunker / embedder / store / service。

全程使用 MockEmbedder（离线确定性向量），零网络依赖。
"""

from __future__ import annotations

import math

import pytest

from tianshu.rag.chunker import chunk_text
from tianshu.rag.embedder import MockEmbedder, create_embedder
from tianshu.rag.store import HybridStore, _fts_query, _seg_cjk
from tianshu.rag.service import RAGService


# ── Fixtures ──────────────────────────────────────────────────────────────

def mock_config(tmp_path) -> dict:
    return {
        "embedding": {"provider": "mock", "dims": 64},
        "storage": {"dir": str(tmp_path)},
        "chunking": {"size": 200, "overlap": 40},
        "search": {"top_k": 3, "vector_weight": 0.5},
    }


@pytest.fixture
def service(tmp_path) -> RAGService:
    return RAGService(mock_config(tmp_path))


# ── chunker ───────────────────────────────────────────────────────────────

class TestChunker:
    def test_markdown_titles(self):
        text = "# 标题一\n\n内容甲。\n\n## 标题二\n\n内容乙。"
        chunks = chunk_text(text, source="test.md", size=200, overlap=40)
        titles = [c.title for c in chunks]
        assert "标题一" in titles
        assert "标题二" in titles
        assert all(c.source == "test.md" for c in chunks)

    def test_index_sequential(self):
        text = "\n\n".join(f"段落 {i} 的测试内容。" for i in range(10))
        chunks = chunk_text(text, size=50, overlap=10)
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_long_single_paragraph_slides(self):
        text = "。".join(f"第{i}句测试内容" for i in range(500))
        chunks = chunk_text(text, size=200, overlap=40)
        assert len(chunks) > 5
        # 每个 chunk 不超过 size（单句本身可能略超，给 50 字符容差）
        for c in chunks:
            assert len(c.text) <= 250

    def test_overlap_carried(self):
        # 两个窗口间应共享重叠内容
        text = "。".join(f"句子编号{i}内容" for i in range(100))
        chunks = chunk_text(text, size=100, overlap=30)
        assert len(chunks) >= 2
        shared = [t for t in chunks[0].text.split("。") if t in chunks[1].text]
        assert len(shared) >= 1

    def test_empty_text(self):
        assert chunk_text("", size=100, overlap=20) == []

    def test_size_must_exceed_overlap(self):
        with pytest.raises(ValueError):
            chunk_text("文本", size=50, overlap=60)

    def test_chunk_id_deterministic(self):
        text = "同样的内容。"
        a = chunk_text(text, source="f.md", size=100, overlap=20)[0]
        b = chunk_text(text, source="f.md", size=100, overlap=20)[0]
        assert a.chunk_id == b.chunk_id


# ── embedder ──────────────────────────────────────────────────────────────

class TestEmbedder:
    @pytest.mark.asyncio
    async def test_mock_deterministic_and_normalized(self):
        emb = MockEmbedder(dims=64)
        v1 = (await emb.embed(["天枢是 AI Agent 框架"]))[0]
        v2 = (await emb.embed(["天枢是 AI Agent 框架"]))[0]
        assert v1 == v2
        norm = math.sqrt(sum(x * x for x in v1))
        assert abs(norm - 1.0) < 1e-6

    @pytest.mark.asyncio
    async def test_mock_similar_texts_closer(self):
        emb = MockEmbedder(dims=128)
        q = (await emb.embed(["天枢 Agent 框架 知识库"]))[0]
        hit = (await emb.embed(["天枢 Agent 框架 知识库 文档 检索"]))[0]
        miss = (await emb.embed(["完全无关的其他内容"]))[0]
        sim_hit = sum(a * b for a, b in zip(q, hit))
        sim_miss = sum(a * b for a, b in zip(q, miss))
        assert sim_hit > sim_miss

    def test_create_embedder_offline_fallback(self):
        emb, offline = create_embedder({"embedding": {"provider": "zhipu", "api_key": "${ZHIPU_API_KEY}"}})
        assert offline is True
        assert isinstance(emb, MockEmbedder)

    def test_create_embedder_mock_explicit(self):
        emb, offline = create_embedder({"embedding": {"provider": "mock", "dims": 32}})
        assert offline is True
        assert emb.dims == 32

    def test_create_embedder_api(self):
        cfg = {"embedding": {"provider": "zhipu", "model": "embedding-3",
                             "base_url": "https://open.bigmodel.cn/api/paas/v4",
                             "api_key": "test-key"}}
        emb, offline = create_embedder(cfg)
        assert offline is False
        assert emb.model == "embedding-3"
        assert emb.endpoint == "https://open.bigmodel.cn/api/paas/v4/embeddings"


# ── store ─────────────────────────────────────────────────────────────────

async def _add_docs(store: HybridStore, collection: str = "default") -> None:
    emb = MockEmbedder(dims=64)
    docs = [
        "天枢是中国本土自主的 AI Agent 框架，采用三爻架构。",
        "RAG 知识库支持 PDF 和 Markdown 文档的语义检索。",
        "苹果公司今天发布了新款手机。",
    ]
    vectors = await emb.embed(docs)
    await store.add(collection, [
        {"chunk_id": f"d{i}", "text": t, "source": f"doc{i}.md", "title": f"标题{i}", "vector": v}
        for i, (t, v) in enumerate(zip(docs, vectors))
    ])


class TestStore:
    @pytest.mark.asyncio
    async def test_keyword_search(self, tmp_path):
        store = HybridStore(tmp_path / "rag.db")
        await _add_docs(store)
        hits = await store.search_keyword("default", "AI Agent 框架", k=3)
        assert hits
        assert "三爻架构" in hits[0]["text"]
        assert hits[0]["rank"] == 1

    @pytest.mark.asyncio
    async def test_keyword_search_chinese_bigram(self, tmp_path):
        store = HybridStore(tmp_path / "rag.db")
        await _add_docs(store)
        hits = await store.search_keyword("default", "知识库检索", k=3)
        assert hits
        assert "RAG" in hits[0]["text"]

    @pytest.mark.asyncio
    async def test_vector_search(self, tmp_path):
        store = HybridStore(tmp_path / "rag.db")
        await _add_docs(store)
        emb = MockEmbedder(dims=64)
        q = (await emb.embed(["知识库 语义检索 文档"]))[0]
        hits = await store.search_vector("default", q, k=1)
        assert "RAG 知识库" in hits[0]["text"]

    @pytest.mark.asyncio
    async def test_hybrid_fusion(self, tmp_path):
        store = HybridStore(tmp_path / "rag.db")
        await _add_docs(store)
        emb = MockEmbedder(dims=64)
        q = (await emb.embed(["AI Agent 框架"]))[0]
        hits = await store.search_hybrid("default", "AI Agent 框架", q, k=3, vector_weight=0.5)
        assert hits
        assert "三爻架构" in hits[0]["text"]
        assert "fusion" in hits[0]
        assert [h["rank"] for h in hits] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_dedup_on_readd(self, tmp_path):
        store = HybridStore(tmp_path / "rag.db")
        await _add_docs(store)
        # 再次写入相同 chunk_id → 全部跳过
        emb = MockEmbedder(dims=64)
        v = (await emb.embed(["重复内容。"]))[0]
        added = await store.add("default", [
            {"chunk_id": "d0", "text": "重复内容。", "source": "", "title": "", "vector": v}
        ])
        assert added == 0

    @pytest.mark.asyncio
    async def test_collections_and_delete(self, tmp_path):
        store = HybridStore(tmp_path / "rag.db")
        await _add_docs(store, "a")
        await _add_docs(store, "b")
        cols = await store.collections()
        assert {c["name"] for c in cols} == {"a", "b"}
        assert all(c["chunks"] == 3 for c in cols)
        n = await store.delete_collection("a")
        assert n == 3
        assert await store.chunk_count("a") == 0
        assert await store.chunk_count("b") == 3

    @pytest.mark.asyncio
    async def test_search_empty_collection(self, tmp_path):
        store = HybridStore(tmp_path / "rag.db")
        emb = MockEmbedder(dims=64)
        q = (await emb.embed(["任意"]))[0]
        assert await store.search_vector("none", q) == []
        assert await store.search_keyword("none", "任意") == []
        assert await store.search_hybrid("none", "任意", q) == []

    @pytest.mark.asyncio
    async def test_search_skips_dim_mismatch(self, tmp_path):
        """embedding 配置变更后（如 mock 256 维 → API 2048 维）旧 chunk 不参与向量检索。"""
        store = HybridStore(tmp_path / "rag.db")
        await _add_docs(store)
        # 128 维查询向量 vs 存储的 64 维 → 跳过全部，不报错
        emb = MockEmbedder(dims=128)
        q = (await emb.embed(["AI Agent"]))[0]
        assert await store.search_vector("default", q) == []
        # 关键词检索不受影响
        hits = await store.search_keyword("default", "AI Agent")
        assert hits


# ── 中文分词工具 ──────────────────────────────────────────────────────────

class TestSegCjk:
    def test_seg_cjk(self):
        assert _seg_cjk("知识库") == "知识 识库"
        assert _seg_cjk("天") == "天"
        assert _seg_cjk("hello 世界") == "hello 世界"  # 双字才切

    def test_fts_query(self):
        q = _fts_query("AI Agent 知识库")
        assert '"ai"' in q and '"agent"' in q
        assert '"知识"' in q and '"识库"' in q
        assert " OR " in q

    def test_fts_query_empty(self):
        assert _fts_query("!!!") == ""


# ── service ───────────────────────────────────────────────────────────────

class TestService:
    @pytest.mark.asyncio
    async def test_ingest_and_search_roundtrip(self, service, tmp_path):
        doc = tmp_path / "docs"
        doc.mkdir()
        (doc / "arch.md").write_text(
            "# 架构\n\n天枢采用三爻架构：天曜治理、人曜决策、地曜执行。\n\n"
            "## 安全\n\n三道闸门保障工具执行安全。",
            encoding="utf-8",
        )
        r = await service.ingest_path(doc)
        assert r["files"] == 1
        assert r["chunks"] >= 1
        assert r["added"] == r["chunks"]

        hits = await service.search("三爻架构是什么", top_k=3)
        assert hits
        assert "三爻架构" in hits[0]["text"]

    @pytest.mark.asyncio
    async def test_ingest_repeat_is_idempotent(self, service, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("# 笔记\n\nRAG 知识库测试内容。", encoding="utf-8")
        first = await service.ingest_path(f)
        second = await service.ingest_path(f)
        assert first["added"] > 0
        assert second["added"] == 0

    @pytest.mark.asyncio
    async def test_ingest_missing_path_raises(self, service, tmp_path):
        with pytest.raises(FileNotFoundError):
            await service.ingest_path(tmp_path / "不存在")

    @pytest.mark.asyncio
    async def test_status_and_delete(self, service, tmp_path):
        (tmp_path / "x.md").write_text("测试内容一。", encoding="utf-8")
        await service.ingest_path(tmp_path / "x.md", collection="c1")
        st = await service.status()
        assert st["offline"] is True
        assert st["embedder"] == "mock"
        assert any(c["name"] == "c1" for c in st["collections"])
        n = await service.delete_collection("c1")
        assert n >= 1
        st2 = await service.status()
        assert all(c["name"] != "c1" for c in st2["collections"])

    @pytest.mark.asyncio
    async def test_search_empty_query(self, service):
        assert await service.search("  ") == []


# ── Skill 工具注册 ────────────────────────────────────────────────────────

class TestRagSkill:
    def test_tools_registered(self):
        from tianshu.renyao.skills.rag import RAGSkill
        names = [t.name for t in RAGSkill().get_tools()]
        assert names == ["rag_ingest", "rag_search", "rag_status", "rag_delete"]

    def test_loader_includes_rag(self):
        from tianshu.renyao.skills.loader import SkillLoader
        loader = SkillLoader()
        loader.load_builtins()
        assert "rag" in loader._skills
