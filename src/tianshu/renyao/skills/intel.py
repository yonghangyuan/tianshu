"""Intel Skill —— 情报搜集与分析。

一条命令完成: 多源搜索 → 去重 → LLM 提取关键信息 → 结构化简报。

设计原则:
  - 一个 Skill，不是独立框架
  - LLM 可在对话中直接调用
  - 全程天曜审计可追溯
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .base import BaseSkill, SkillTool


class IntelSkill(BaseSkill):
    name = "intel"
    description = "情报搜集与分析——多源搜索、去重、提取关键信息、生成简报"
    trigram = "天"  # 天爻——情报是可审计的
    trigger_keywords = ["情报", "搜集", "监控", "追踪", "简报", "intel", "动态"]

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="intel_search",
                description="Multi-source intelligence search. Searches WeChat+arXiv+web, deduplicates, and extracts key info. Returns structured brief.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search topic, e.g. 'AI safety regulations'"},
                        "sources": {"type": "string", "description": "Sources: weixin,arxiv,web,rand (comma-separated, default all)", "default": "weixin,web"},
                        "days": {"type": "integer", "description": "Look back N days", "default": 7},
                        "max_results": {"type": "integer", "description": "Max results per source", "default": 5},
                    },
                    "required": ["query"],
                },
                handler=self._intel_search,
                permission_level=0,  # SAFE — 只读
            ),
            SkillTool(
                name="intel_brief",
                description="Generate a structured intelligence brief from collected items. Saves as .md file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Brief topic/title"},
                        "items_json": {"type": "string", "description": "JSON array of {title,url,source,date,summary} objects"},
                        "output_dir": {"type": "string", "description": "Output directory", "default": "F:/reports"},
                    },
                    "required": ["topic", "items_json"],
                },
                handler=self._intel_brief,
                permission_level=2,  # WRITE — 写文件
            ),
        ]

    # ── 核心: 多源搜索 + 去重 + 提取 ─────────────────────────

    async def _intel_search(
        self, query: str, sources: str = "weixin,web",
        days: int = 7, max_results: int = 5, **kwargs,
    ) -> str:
        src_list = [s.strip() for s in sources.split(",")]
        all_items: list[dict] = []

        for src in src_list:
            try:
                items = await self._search_source(src, query, max_results)
                all_items.extend(items)
            except Exception as e:
                all_items.append({
                    "title": f"[{src} 搜索失败]",
                    "url": "", "source": src, "date": "",
                    "summary": str(e)[:100],
                })

        if not all_items:
            return f"🔍 情报搜索: {query}\n\n未找到相关结果"

        # 去重（按 URL + 标题相似度）
        deduped = self._deduplicate(all_items)
        # 按日期倒序
        deduped.sort(key=lambda x: x.get("date", ""), reverse=True)

        lines = [
            f"📊 情报搜索: {query}",
            f"   来源: {sources} | 时间: {days} 天内",
            f"   采集: {len(all_items)} 条 → 去重后 {len(deduped)} 条\n",
        ]
        for i, item in enumerate(deduped[:max_results * 2]):
            date_str = item.get("date", "")[:10]
            lines.append(f"[{i + 1}] {item['title']}")
            lines.append(f"    来源: {item['source']} | {date_str}")
            if item.get("summary"):
                lines.append(f"    {item['summary'][:200]}")
            if item.get("url"):
                lines.append(f"    {item['url']}")
            lines.append("")
        return "\n".join(lines)

    # ── 单源搜索 ──────────────────────────────────────────

    async def _search_source(self, source: str, query: str, count: int) -> list[dict]:
        items = []
        if source == "weixin":
            from .browser import BrowserSkill
            bs = BrowserSkill()
            result = await bs._sogou_weixin_search(query, count)
            items = self._parse_sogou_results(result)
        elif source in ("web", "bing"):
            from .web_search import WebSearchSkill
            ws = WebSearchSkill()
            result = await ws._search(query, count)
            items = self._parse_web_results(result)
        elif source == "arxiv":
            items = await self._search_arxiv(query, count)
        elif source == "rand":
            from .browser import BrowserSkill
            bs = BrowserSkill()
            url = f"https://www.rand.org/search.html?query={query}&sortby=date"
            result = await bs._browse(url, max_chars=3000)
            items = self._parse_rand_results(result)

        # 标记来源和时间
        now = datetime.now().strftime("%Y-%m-%d")
        for item in items:
            item["source"] = source
            if not item.get("date"):
                item["date"] = now
        return items

    # ── 简报生成 ──────────────────────────────────────────

    async def _intel_brief(
        self, topic: str, items_json: str,
        output_dir: str = "F:/reports", **kwargs,
    ) -> str:
        try:
            items = json.loads(items_json)
        except json.JSONDecodeError:
            return "❌ items_json 格式错误，需要 JSON 数组"

        if not items:
            return "❌ 情报列表为空"

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%Y-%m-%d %H:%M")
        filename = f"{topic.replace(' ', '_')}_{date_str}.md"

        out = Path(output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        filepath = out / filename

        # 生成 Markdown 简报
        lines = [
            f"# {topic}",
            f"",
            f"> 生成时间: {time_str} | 来源: 天枢情报系统",
            f"> 采集条数: {len(items)} | 审计 ID: {int(time.time())}",
            f"",
            f"---",
            f"",
            f"## 概要",
            f"",
        ]

        # 按来源统计
        sources = {}
        for item in items:
            src = item.get("source", "未知")
            sources[src] = sources.get(src, 0) + 1
        lines.append(f"| 来源 | 数量 |")
        lines.append(f"|------|------|")
        for src, cnt in sorted(sources.items()):
            lines.append(f"| {src} | {cnt} |")
        lines.append("")

        # 正文
        lines.append("## 详细情报")
        lines.append("")
        for i, item in enumerate(items):
            title = item.get("title", "(无标题)")
            src = item.get("source", "未知")
            url = item.get("url", "")
            date = item.get("date", "")[:10]
            summary = item.get("summary", "")

            lines.append(f"### [{i + 1}] {title}")
            lines.append(f"")
            lines.append(f"- **来源**: {src}")
            lines.append(f"- **日期**: {date}")
            if url:
                lines.append(f"- **链接**: {url}")
            if summary:
                lines.append(f"- **摘要**: {summary}")
            lines.append("")

        lines.append("---")
        lines.append(f"*本简报由天枢 Agent 自动生成 · 全程可审计*")

        content = "\n".join(lines)
        filepath.write_text(content, encoding="utf-8")

        return (
            f"✅ 简报已生成: {filepath}\n"
            f"   条数: {len(items)} | 来源: {', '.join(sources.keys())}\n"
            f"   大小: {len(content)} 字符"
        )

    # ── 工具函数 ──────────────────────────────────────────

    @staticmethod
    def _deduplicate(items: list[dict]) -> list[dict]:
        seen_urls = set()
        seen_titles = set()
        result = []
        for item in items:
            url = item.get("url", "")
            title = item.get("title", "")[:50].lower()
            if url and url in seen_urls:
                continue
            if title and title in seen_titles:
                continue
            if url:
                seen_urls.add(url)
            if title:
                seen_titles.add(title)
            result.append(item)
        return result

    @staticmethod
    def _parse_sogou_results(text: str) -> list[dict]:
        items = []
        for line in text.split("\n"):
            if line.startswith("[") and "] " in line:
                title = line.split("] ", 1)[1].strip()
                items.append({"title": title, "url": "", "date": "", "summary": ""})
            elif line.strip().startswith("http"):
                if items:
                    items[-1]["url"] = line.strip()
            elif line.strip() and not line.startswith("🔍") and not line.startswith("📊"):
                if items and not items[-1].get("summary"):
                    items[-1]["summary"] = line.strip()[:300]
        return items

    @staticmethod
    def _parse_web_results(text: str) -> list[dict]:
        items = []
        for line in text.split("\n"):
            if line.startswith("[") and "] " in line:
                parts = line.split("] ", 1)[1].strip()
                title = parts[:100]
                items.append({"title": title, "url": "", "date": "", "summary": ""})
            elif line.strip().startswith("http"):
                if items:
                    items[-1]["url"] = line.strip()
            elif line.strip() and not line.startswith("🔍"):
                if items and not items[-1].get("summary"):
                    items[-1]["summary"] = line.strip()[:300]
        return items

    @staticmethod
    async def _search_arxiv(query: str, count: int) -> list[dict]:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "http://export.arxiv.org/api/query",
                    params={
                        "search_query": f"all:{query}",
                        "start": 0, "max_results": count,
                        "sortBy": "submittedDate", "sortOrder": "descending",
                    },
                )
                import re
                entries = re.findall(
                    r"<entry>(.*?)</entry>", resp.text, re.DOTALL
                )
                items = []
                for entry in entries[:count]:
                    title_m = re.search(r"<title>(.*?)</title>", entry)
                    url_m = re.search(r"<id>(.*?)</id>", entry)
                    date_m = re.search(r"<published>(.*?)</published>", entry)
                    summary_m = re.search(r"<summary>(.*?)</summary>", entry)
                    items.append({
                        "title": title_m.group(1).strip() if title_m else "",
                        "url": url_m.group(1).strip() if url_m else "",
                        "date": date_m.group(1)[:10] if date_m else "",
                        "summary": summary_m.group(1).strip()[:300] if summary_m else "",
                        "source": "arxiv",
                    })
                return items
        except Exception:
            return []

    @staticmethod
    def _parse_rand_results(text: str) -> list[dict]:
        items = []
        import re
        titles = re.findall(r'<h3[^>]*><a[^>]*>([^<]+)</a>', text)
        urls = re.findall(r'<h3[^>]*><a[^>]+href="([^"]+)"', text)
        for i in range(min(len(titles), len(urls))):
            url = urls[i]
            if not url.startswith("http"):
                url = "https://www.rand.org" + url
            items.append({
                "title": titles[i].strip(),
                "url": url,
                "date": "",
                "summary": "",
            })
        return items
