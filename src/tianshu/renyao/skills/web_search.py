"""Web Search Skill — DuckDuckGo + 多后端 fallback。"""

import urllib.parse
import httpx
from .base import BaseSkill, SkillTool


class WebSearchSkill(BaseSkill):
    name = "web-search"
    description = "搜索互联网信息，返回摘要和链接（DDG → Bing fallback）"
    trigram = "地"
    trigger_keywords = ["搜索", "search", "查一下", "帮我搜"]

    def get_tools(self) -> list[SkillTool]:
        return [SkillTool(
            name="web_search",
            description="Search the web and return results with titles, snippets, and URLs.",
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
        # 尝试顺序: DDG API → DDG HTML → 返回搜索链接
        result = await self._try_ddg_api(query, count)
        if result and "Search failed" not in result and "No results" not in result:
            return result
        result = await self._try_ddg_html(query, count)
        if result and "Search failed" not in result:
            return result
        # 最终 fallback: 给用户搜索链接
        return self._search_links(query)

    async def _try_ddg_api(self, query: str, count: int) -> str:
        """DuckDuckGo Instant Answer API。"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": 1, "t": "tianshu"},
                    headers={"User-Agent": "TianshuAgent/0.1"},
                )
                resp.raise_for_status()
                data = resp.json()
                results = []
                for r in data.get("Results", [])[:count]:
                    results.append(f"- {r.get('Text', '')}\n  {r.get('FirstURL', '')}")
                for r in data.get("RelatedTopics", [])[:count - len(results)]:
                    if isinstance(r, dict) and r.get("Text"):
                        results.append(f"- {r['Text']}\n  {r.get('FirstURL', '')}")
                return "\n\n".join(results) if results else f"[DDG API] No results for: {query}"
        except Exception as e:
            return f"[DDG API] Failed: {e}"

    async def _try_ddg_html(self, query: str, count: int) -> str:
        """DuckDuckGo HTML 搜索（需要解析 HTML，简化版）。"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "Mozilla/5.0 TianshuAgent/0.1"},
                )
                resp.raise_for_status()
                # 简易解析: 找 class=result__snippet 和 class=result__url
                import re
                html = resp.text
                snippets = re.findall(
                    r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL
                )
                urls = re.findall(
                    r'class="result__url"[^>]*>(.*?)</a>', html, re.DOTALL
                )
                results = []
                for i in range(min(count, len(snippets))):
                    s = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                    u = urls[i].strip() if i < len(urls) else ""
                    results.append(f"- {s[:200]}\n  {u}")
                return "\n\n".join(results) if results else f"[DDG HTML] No results"
        except Exception as e:
            return f"[DDG HTML] Failed: {e}"

    def _search_links(self, query: str) -> str:
        """返回手动搜索链接（无 API 时的最后手段）。"""
        q = urllib.parse.quote(query)
        return (
            f"⚠️ 搜索服务暂时不可用。请手动搜索：\n"
            f"  DuckDuckGo: https://duckduckgo.com/?q={q}\n"
            f"  Bing:       https://www.bing.com/search?q={q}\n"
            f"  Google:     https://www.google.com/search?q={q}\n"
            f" 百度:        https://www.baidu.com/s?wd={q}"
        )
