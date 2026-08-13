"""Embedding Provider — 文本 → 向量。

主路径: OpenAI 兼容 /embeddings 端点（智谱 embedding-3 / 豆包 / 硅基流动 BGE 等）
离线路径: MockEmbedder — 确定性哈希向量，零网络零 Key，测试与演示用。
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from typing import Any


class BaseEmbedder(ABC):
    """Embedding 抽象接口。"""
    name: str = "base"

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本 → 向量列表（维度一致）。"""


# ── 离线 Mock ─────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """中英混合分词：英文按词，中文按 2-gram。"""
    tokens: list[str] = []
    for word in re.findall(r"[a-zA-Z0-9_]+", text.lower()):
        tokens.append(word)
    for run in re.findall(r"[一-鿿]+", text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


class MockEmbedder(BaseEmbedder):
    """确定性 mock embedding — 词袋哈希 + L2 归一化。

    同词越多 → 余弦相似度越高，可验证检索链路行为。
    仅用于测试与离线演示；生产请配置真实 embedding API。
    """
    name = "mock"

    def __init__(self, dims: int = 256) -> None:
        self.dims = dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dims
        for tok in _tokenize(text):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
            vec[h % self.dims] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


# ── API Embedder ──────────────────────────────────────────────────────────

class OpenAICompatEmbedder(BaseEmbedder):
    """OpenAI 兼容 embedding API 适配器。

    智谱: base_url=https://open.bigmodel.cn/api/paas/v4, model=embedding-3
    豆包: base_url=https://ark.cn-beijing.volces.com/api/v3, model=doubao-embedding-large
    硅基流动: base_url=https://api.siliconflow.cn/v1, model=BAAI/bge-m3
    """
    name = "openai-compat"

    def __init__(self, model: str, base_url: str, api_key: str = "",
                 batch_size: int = 16, timeout: float = 60.0) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.batch_size = max(1, batch_size)
        self.timeout = timeout
        self.last_error = ""

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/embeddings"):
            return self.base_url
        return f"{self.base_url}/embeddings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i:i + self.batch_size]
                resp = await client.post(
                    self.endpoint,
                    headers=headers,
                    json={"model": self.model, "input": batch},
                )
                if resp.status_code != 200:
                    self.last_error = f"embedding API {resp.status_code}: {resp.text[:200]}"
                    raise RuntimeError(self.last_error)
                data = resp.json().get("data", [])
                if len(data) != len(batch):
                    raise RuntimeError(
                        f"embedding 返回数量不匹配: 期望 {len(batch)}, 实际 {len(data)}"
                    )
                vectors.extend([float(x) for x in item["embedding"]] for item in data)
        return vectors


# ── 工厂 ──────────────────────────────────────────────────────────────────

def create_embedder(config: dict[str, Any]) -> tuple[BaseEmbedder, bool]:
    """从 rag.yaml 的 embedding 配置构造 embedder。

    Returns:
        (embedder, offline): offline=True 表示未配置 API Key，降级为 Mock。
    """
    cfg = config.get("embedding", {}) or {}
    provider = (cfg.get("provider") or "").lower()
    if provider == "mock":
        return MockEmbedder(dims=int(cfg.get("dims", 256))), True

    api_key = cfg.get("api_key", "") or ""
    if not api_key or api_key.startswith("${"):
        # 环境变量未配置 → 离线降级
        return MockEmbedder(dims=int(cfg.get("dims", 256))), True

    return OpenAICompatEmbedder(
        model=cfg.get("model", "embedding-3"),
        base_url=cfg.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
        api_key=api_key,
        batch_size=int(cfg.get("batch_size", 16)),
    ), False
