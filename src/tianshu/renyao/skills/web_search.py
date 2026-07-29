"""Web Search Skill — 自建搜索引擎，直接请求搜索引擎 HTML 页面并解析结果。

零外部 API 依赖。不经过 DuckDuckGo / Google API / 任何第三方服务。
用 httpx 直接 GET 搜索引擎的 HTML 结果页，解析后返回结构化结果。
"""

from __future__ import annotations

import re
import urllib.parse

import httpx

from .base import BaseSkill, SkillTool
from .browser import _extract_text, _entity_decode


class WebSearchSkill(BaseSkill):
    name = "web-search"
    description = "自建搜索引擎——直接请求 Bing/百度 HTML 结果页，解析返回。零外部 API。"
    trigram = "地"
    trigger_keywords = ["搜索", "search", "查一下", "帮我搜"]

    def get_tools(self) -> list[SkillTool]:
        return [SkillTool(
            name="web_search",
            description="Search the web by directly fetching search engine HTML pages and parsing results. No external API used.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "count": {"type": "integer", "description": "Number of results", "default": 5},
                },
                "required": ["query"],
            },
            handler=self._search,
        )]

    async def _search(self, query: str, count: int = 5) -> str:
        # 依次尝试多个搜索引擎
        for engine in (self._search_bing, self._search_baidu):
            try:
                result = await engine(query, count)
                if result and len(result) > 50:
                    return result
            except Exception:
                continue

        # 全部失败 → 给搜索链接
        q = urllib.parse.quote(query)
        return (
            f"⚠️ 搜索引擎暂时无法访问。请手动搜索：\n"
            f"  Bing:  https://www.bing.com/search?q={q}\n"
            f"  百度:  https://www.baidu.com/s?wd={q}\n"
            f"  Google: https://www.google.com/search?q={q}"
        )

    # ── Bing ────────────────────────────────────────────────────────

    async def _search_bing(self, query: str, count: int) -> str:
        url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&count={count}"
        html = await self._fetch(url)
        if not html:
            return ""

        results = _parse_bing_results(html, count)
        if not results:
            return ""

        lines = [f"🔍 Bing: {query}\n"]
        for i, r in enumerate(results):
            lines.append(f"[{i + 1}] {r['title']}")
            lines.append(f"    {r['snippet'][:200]}")
            lines.append(f"    {r['url']}")
            lines.append("")
        return "\n".join(lines)

    # ── 百度 ──────────────────────────────────────────────────────

    async def _search_baidu(self, query: str, count: int) -> str:
        url = f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}&rn={count}"
        html = await self._fetch(url)
        if not html:
            return ""

        results = _parse_baidu_results(html, count)
        if not results:
            return ""

        lines = [f"🔍 百度: {query}\n"]
        for i, r in enumerate(results):
            lines.append(f"[{i + 1}] {r['title']}")
            lines.append(f"    {r['snippet'][:200]}")
            lines.append(f"    {r['url']}")
            lines.append("")
        return "\n".join(lines)

    # ── HTTP ───────────────────────────────────────────────────────

    async def _fetch(self, url: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/html,application/xhtml+xml,*/*",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    },
                )
                resp.raise_for_status()
                return resp.text
        except Exception:
            return ""


# ═══════════════════════════════════════════════════════════════════════════
# HTML 解析 — 搜索引擎结果页
# ═══════════════════════════════════════════════════════════════════════════

def _parse_bing_results(html: str, count: int) -> list[dict]:
    """解析 Bing 搜索结果页。"""
    results = []
    # Bing 结果在 <li class="b_algo"> 中
    blocks = re.findall(
        r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.DOTALL
    )
    for block in blocks[:count]:
        title_match = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>([^<]+(?:<[^>]+>[^<]*)*)</a>', block)
        if not title_match:
            continue
        url = title_match.group(1)
        title = _clean(title_match.group(2))
        # snippet: <p> or <div class="b_caption">
        snippet_match = re.search(
            r'<(?:p|div class="b_caption[^"]*")[^>]*>(.*?)</(?:p|div)>',
            block, re.DOTALL
        )
        snippet = _clean(snippet_match.group(1)[:300]) if snippet_match else ""
        results.append({"title": title, "snippet": snippet, "url": url})
    return results


def _parse_baidu_results(html: str, count: int) -> list[dict]:
    """解析百度搜索结果页。"""
    results = []
    # 百度结果在 <div class="result c-container"> 或 <div class="c-container">
    blocks = re.findall(
        r'<div[^>]*class="[^"]*c-container[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        html, re.DOTALL
    )
    if not blocks:
        # 兼容移动版
        blocks = re.findall(
            r'<div[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</div>\s*</div>',
            html, re.DOTALL
        )

    for block in blocks[:count]:
        # 标题: <h3><a href="...">title</a></h3>
        title_match = re.search(
            r'<a[^>]+href="([^"]+)"[^>]*>([^<]+(?:<[^>]+>[^<]*)*)</a>',
            block
        )
        if not title_match:
            continue
        url = title_match.group(1)
        title = _clean(title_match.group(2))
        # 摘要: <span class="content-right_..."> 或 <div class="c-abstract">
        snippet_match = re.search(
            r'<(?:span|div)[^>]*class="[^"]*(?:content-right|c-abstract|article)[^"]*"[^>]*>(.*?)</(?:span|div)>',
            block, re.DOTALL
        )
        snippet = _clean(snippet_match.group(1)[:300]) if snippet_match else ""
        results.append({"title": title, "snippet": snippet, "url": url})
    return results


def _clean(s: str) -> str:
    """去标签 + 实体解码。"""
    s = re.sub(r"<[^>]+>", " ", s)
    s = _entity_decode(s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()
