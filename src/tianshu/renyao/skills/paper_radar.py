"""论文雷达 Skill — 论文搜索、下载、分类、深度笔记。

三爻分类: 地+人（数据获取 + 分析决策）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .base import BaseSkill, SkillTool


class PaperRadarSkill(BaseSkill):
    name = "paper-radar"
    description = "论文搜索、下载 PDF、按分类体系打标签、写深度笔记"
    trigram = "地+人"
    trigger_keywords = [
        "论文", "paper", "文献", "arxiv", "搜索论文", "下载论文",
        "论文分类", "论文雷达", "paper radar", "文献追踪",
    ]

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="search_papers",
                description="在 arXiv 上搜索论文。返回论文元数据列表（id, title, authors, year, abstract）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "max_results": {"type": "integer", "description": "最大返回数量，默认 5", "default": 5},
                    },
                    "required": ["query"],
                },
                handler=self._search,
            ),
            SkillTool(
                name="download_pdf",
                description="下载论文 PDF 到本地。",
                parameters={
                    "type": "object",
                    "properties": {
                        "arxiv_id": {"type": "string", "description": "arXiv ID，如 2605.14851"},
                    },
                    "required": ["arxiv_id"],
                },
                handler=self._download,
                permission_level=2,  # WRITE
            ),
            SkillTool(
                name="write_paper_notes",
                description="为论文创建标准化的阅读笔记（Frontmatter + Deep Notes）。保存到 趋势追踪/研究论文/。",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "论文标题"},
                        "arxiv_id": {"type": "string", "description": "arXiv ID"},
                        "authors": {"type": "string", "description": "作者列表，逗号分隔"},
                        "year": {"type": "integer", "description": "发表年份"},
                        "abstract": {"type": "string", "description": "论文摘要"},
                        "venue": {"type": "string", "description": "发表 venue（可选）"},
                        "notes": {"type": "string", "description": "Deep Notes 内容（问题/方法/贡献/局限/启发）"},
                    },
                    "required": ["title", "arxiv_id", "authors", "year", "abstract", "notes"],
                },
                handler=self._write_notes,
                permission_level=2,  # WRITE
            ),
        ]

    # ── 工具实现 ─────────────────────────────────────────────────────

    async def _search(self, query: str, max_results: int = 5, **kwargs) -> str:
        """搜索 arXiv。复用 paper-radar 的 search 模块。"""
        # 尝试直接调 arxiv API
        import httpx
        url = f"https://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as e:
            return f"arXiv API 请求失败: {e}"

        # 简易 XML 解析
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)

        results = []
        for entry in entries[:max_results]:
            arxiv_id = entry.find("atom:id", ns).text.split("/")[-1] if entry.find("atom:id", ns) is not None else "?"
            title = " ".join(entry.find("atom:title", ns).text.split()) if entry.find("atom:title", ns) is not None else "?"
            authors = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns) if a.find("atom:name", ns) is not None]
            abstract = " ".join(entry.find("atom:summary", ns).text.split())[:500] if entry.find("atom:summary", ns) is not None else ""
            published = entry.find("atom:published", ns).text if entry.find("atom:published", ns) is not None else ""
            year = published[:4] if published else "?"

            results.append({
                "id": arxiv_id,
                "title": title,
                "authors": ", ".join(authors[:5]),
                "year": year,
                "abstract": abstract[:300],
            })

        if not results:
            return f"未找到与 '{query}' 相关的论文"

        lines = [f"搜索 '{query}' — 找到 {len(results)} 篇:"]
        for i, r in enumerate(results, 1):
            lines.append(f"  {i}. [{r['id']}] {r['title'][:80]} ({r['year']}) — {r['authors'][:60]}")
        return "\n".join(lines)

    async def _download(self, arxiv_id: str, **kwargs) -> str:
        """下载 PDF。"""
        pdf_dir = Path("F:/趋势追踪/研究论文/_pdfs")
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / f"{arxiv_id}.pdf"

        if pdf_path.exists():
            return f"PDF 已存在: {pdf_path} ({pdf_path.stat().st_size // 1024} KB)"

        import httpx
        url = f"https://arxiv.org/pdf/{arxiv_id}"
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                pdf_path.write_bytes(resp.content)
                return f"已下载: {pdf_path} ({len(resp.content) // 1024} KB)"
        except Exception as e:
            return f"下载失败: {e}"

    async def _write_notes(
        self, title: str, arxiv_id: str, authors: str, year: int,
        abstract: str, notes: str, venue: str = "",
    ) -> str:
        """写阅读笔记。"""
        # 确定月份和星期
        now = datetime.now()
        month_dir = f"{now.year}.{now.month:02d}"
        # 简化：用当前 ISO 周
        week_num = now.isocalendar()[1]
        # 推算本月第几周
        first_day = now.replace(day=1)
        first_week = first_day.isocalendar()[1]
        month_week = week_num - first_week + 1
        w = f"W{month_week}"

        base = Path(f"F:/趋势追踪/研究论文/{month_dir}/{w}")
        base.mkdir(parents=True, exist_ok=True)

        # Frontmatter
        fm = f"""---
id: "{arxiv_id}"
title: "{title}"
authors:
  - name: "{authors}"
year: {year}
abstract: "{abstract[:200]}"
venue: "{venue}"
venue_type: "preprint"
language: "en"
primary_field: null
sub_fields: []
keywords: []
quality_tier: 2
relevance: "medium"
source_url: "https://arxiv.org/abs/{arxiv_id}"
status: "to_read"
notes_quality: "summary"
last_updated: "{now.strftime('%Y-%m-%d')}"
---

# {title}

{notes}

---

*收录日期: {now.strftime('%Y-%m-%d')}*
"""
        note_path = base / f"arXiv_{arxiv_id}_阅读笔记.md"
        note_path.write_text(fm, encoding="utf-8")
        return f"笔记已保存: {note_path}"
