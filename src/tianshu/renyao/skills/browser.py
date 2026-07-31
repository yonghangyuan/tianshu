"""Browser Skill — 最简浏览器：浏览网页 + 下载 + 上传。

用 httpx（已有依赖）+ 简易 HTML 解析，无需 Playwright。
提供「阅读模式」——提取网页标题、正文文本、链接，去掉广告和脚本。
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

import httpx

from .base import BaseSkill, SkillTool


class BrowserSkill(BaseSkill):
    name = "browser"
    description = "浏览网页、下载文件、上传文件——最简浏览器，阅读模式"
    trigram = "地"
    trigger_keywords = ["浏览", "打开网页", "下载", "上传", "browse", "download"]

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="sogou_weixin",
                description="Search WeChat public account articles via Sogou. Returns real mp.weixin.qq.com URLs with resolved redirect links. Use this instead of browse+sogou for WeChat article search.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword (Chinese or English)"},
                        "count": {"type": "integer", "description": "Number of results", "default": 10},
                    },
                    "required": ["query"],
                },
                handler=self._sogou_weixin_search,
                permission_level=0,
            ),
            SkillTool(
                name="browse",
                description="Open a URL and return page title, text content, and links. Like browser reader mode — strips ads and scripts.",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to open"},
                        "max_chars": {"type": "integer", "description": "Max text characters to return", "default": 8000},
                    },
                    "required": ["url"],
                },
                handler=self._browse,
                permission_level=0,  # SAFE — 只读
            ),
            SkillTool(
                name="download",
                description="Download a file from URL and save to local path. Returns file size and path.",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL of the file to download"},
                        "path": {"type": "string", "description": "Local path to save to (default: infer from URL filename)"},
                    },
                    "required": ["url"],
                },
                handler=self._download,
                permission_level=2,  # WRITE — 写本地文件
            ),
            SkillTool(
                name="upload",
                description="Upload a local file to a URL via HTTP POST/PUT. Returns server response status and body preview.",
                parameters={
                    "type": "object",
                    "properties": {
                        "local_path": {"type": "string", "description": "Path to local file to upload"},
                        "url": {"type": "string", "description": "Upload destination URL"},
                        "method": {"type": "string", "description": "HTTP method: POST or PUT", "default": "POST"},
                        "field_name": {"type": "string", "description": "Form field name for the file (POST only)", "default": "file"},
                    },
                    "required": ["local_path", "url"],
                },
                handler=self._upload,
                permission_level=2,  # WRITE — 发网络请求+读本地文件
            ),
        ]

    # ── Browse (阅读模式) ──────────────────────────────────────────

    async def _browse(self, url: str, max_chars: int = 8000, **kwargs) -> str:
        # 自动补全协议
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 TianshuBrowser/0.2"
                        ),
                        "Accept": "text/html,application/xhtml+xml,*/*",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    },
                )
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type:
                    # 非 HTML：返回原始文本的摘要
                    text = resp.text[:max_chars]
                    return (
                        f"📄 {url}\n"
                        f"Type: {content_type}\n"
                        f"Size: {len(resp.content)} bytes\n"
                        f"{'─' * 60}\n{text}"
                    )

                html = resp.text

        except (httpx.HTTPStatusError, httpx.TimeoutException, Exception) as e:
            # HTTP 错误/超时 → 尝试 Edge CDP（有些网站反爬但 Edge 能打开）
            rendered_text, rendered_title = await BrowserSkill._try_edge_render(url)
            if rendered_text:
                return _fmt_browse_result(
                    rendered_title or url, url, rendered_text, [], max_chars,
                )
            if isinstance(e, httpx.HTTPStatusError):
                return f"❌ HTTP {e.response.status_code}: {url}"
            elif isinstance(e, httpx.TimeoutException):
                return f"❌ 超时: {url} (20s)"
            else:
                return f"❌ 无法访问: {url}\n{type(e).__name__}: {e}"

        # 提取信息
        title = _extract_title(html)
        text = _extract_text(html)
        links = _extract_links(html, url)

        # ── 智能回退：文本太短（JS 渲染页面）→ Edge CDP → Playwright ──
        pw_used = False
        if len(text) < 200:
            # 优先：本地 Edge CDP（零下载）
            rendered_text, rendered_title = await self._try_edge_render(url)
            # 备选：Playwright（如果装了）
            if not rendered_text:
                rendered_text, rendered_title = await self._try_playwright_render(url)
            if rendered_text and len(rendered_text) > len(text):
                text = rendered_text
                title = rendered_title or title
                pw_used = True

        return _fmt_browse_result(title, resp.url, text, links, max_chars)

    # ── 搜狗微信搜索 ──────────────────────────────────────────

    async def _sogou_weixin_search(self, query: str, count: int = 10) -> str:
        """搜索微信公众号文章，返回标题+摘要+真实链接。

        搜狗微信返回内部跳转链接，此方法用 Bing site:mp.weixin.qq.com
        反查每篇文章的真实链接。
        """
        import urllib.parse as _up
        import re as _re

        # 1. 从搜狗获取文章标题和摘要
        encoded = _up.quote(query)
        url = f"https://weixin.sogou.com/weixin?type=2&query={encoded}&ie=utf8"

        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 TianshuBrowser/0.2",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                })
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            return f"❌ 搜狗微信搜索失败: {e}"

        items = _re.findall(
            r'<h3[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>',
            html, re.DOTALL | re.IGNORECASE,
        )

        if not items:
            return f"🔍 搜狗微信: {query}\n\n未找到相关文章"

        results = []
        for href, title_raw, snippet_raw in items[:count]:
            title = _re.sub(r"<[^>]+>", "", title_raw).strip()
            snippet = _re.sub(r"<[^>]+>", "", snippet_raw).strip()

            # 2. 用标题在 Bing 反查真实 mp.weixin.qq.com 链接
            real_url = ""
            try:
                q = _up.quote(f'site:mp.weixin.qq.com {title[:40]}')
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(
                        f"https://cn.bing.com/search?q={q}&count=3",
                        headers={"User-Agent": "Mozilla/5.0 TianshuBrowser/0.2"},
                    )
                    # 找 mp.weixin.qq.com 链接
                    matches = _re.findall(
                        r'<a[^>]+href="(https?://mp\.weixin\.qq\.com[^"]+)"',
                        r.text,
                    )
                    if matches:
                        real_url = matches[0].replace("&amp;", "&")
            except Exception:
                pass

            results.append({
                "title": title,
                "snippet": snippet[:200],
                "url": real_url or f"(需手动搜索) https://www.bing.com/search?q={_up.quote(title)}",
            })

        lines = [f"🔍 搜狗微信: {query}  ({len(results)} 篇)\n"]
        for i, r in enumerate(results):
            lines.append(f"[{i + 1}] {r['title']}")
            if r["snippet"]:
                lines.append(f"    {r['snippet'][:150]}")
            lines.append(f"    {r['url']}")
            lines.append("")
        return "\n".join(lines)

    # ── Edge CDP 渲染（独立自主，零外部下载）─────────────────

    # Edge 调试端口（避免冲突）
    _EDGE_PORT = 9223

    @staticmethod
    async def _try_edge_render(url: str) -> tuple[str, str]:
        """用本地 Edge 浏览器渲染 JS 页面，返回 (文本, 标题)。

        通过 CDP (Chrome DevTools Protocol) 连接 Windows 自带 Edge。
        零外部下载。需要: pip install websockets
        """
        try:
            import websockets
        except ImportError:
            return "", ""

        try:
            import json as _json, subprocess, asyncio, platform, os

            if platform.system() != "Windows":
                return "", ""

            port = BrowserSkill._EDGE_PORT

            # 1. 启动或连接 Edge 调试端口
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    await client.get(f"http://127.0.0.1:{port}/json/version")
            except Exception:
                edge_paths = [
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                ]
                edge_exe = ""
                for ep in edge_paths:
                    if os.path.exists(ep):
                        edge_exe = ep
                        break
                if not edge_exe:
                    return "", ""

                subprocess.Popen(
                    [edge_exe,
                     f"--remote-debugging-port={port}",
                     "--headless=new", "--no-first-run", "--disable-gpu",
                     f"--user-data-dir={os.environ.get('TEMP', '/tmp')}/tianshu_edge"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                await asyncio.sleep(2)

            # 2. 获取 browser WebSocket
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/json/version")
                ws_url = resp.json()["webSocketDebuggerUrl"]

            # 3. CDP: 创建 tab → 导航 → 提取文字
            async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
                recv_queue: asyncio.Queue = asyncio.Queue()

                async def _recv_loop():
                    async for raw in ws:
                        await recv_queue.put(_json.loads(raw))

                recv_task = asyncio.ensure_future(_recv_loop())

                async def _cmd(method: str, params: dict | None = None,
                               sid: str = ""):
                    msg: dict = {"id": 1, "method": method, "params": params or {}}
                    if sid:
                        msg["sessionId"] = sid
                    await ws.send(_json.dumps(msg))
                    # 收集响应（跳过事件）
                    while True:
                        data = await asyncio.wait_for(recv_queue.get(), timeout=15)
                        if data.get("id") == 1:
                            return data.get("result", {})

                # 创建新 tab + 获取 sessionId
                result = await _cmd("Target.createTarget", {"url": "about:blank"})
                target_id = result.get("targetId", "")
                if not target_id:
                    recv_task.cancel()
                    return "", ""

                result = await _cmd("Target.attachToTarget",
                                    {"targetId": target_id, "flatten": True})
                session_id = result.get("sessionId", "")

                # 导航
                await _cmd("Page.enable", sid=session_id)
                await _cmd("Page.navigate", {"url": url}, sid=session_id)
                await asyncio.sleep(2)

                # 提取标题 + 文本
                r = await _cmd("Runtime.evaluate",
                               {"expression": "document.title",
                                "returnByValue": True}, sid=session_id)
                title = r.get("result", {}).get("value", "")

                r = await _cmd("Runtime.evaluate",
                               {"expression": "document.body ? document.body.innerText : ''",
                                "returnByValue": True}, sid=session_id)
                text = r.get("result", {}).get("value", "") or ""

                # 清理：关闭 target
                await _cmd("Target.closeTarget", {"targetId": target_id})
                recv_task.cancel()

            lines = [l.strip() for l in text.split("\n") if l.strip()]
            return "\n".join(lines), title

        except Exception:
            return "", ""

    # ── Playwright 回退（如果装了 Playwright 则优先用）─────────

    @staticmethod
    async def _try_playwright_render(url: str) -> tuple[str, str]:
        """Playwright 回退——如果装了则用。"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return "", ""

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-gpu"],
                )
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)
                title = await page.title()
                text = await page.inner_text("body")
                await browser.close()
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                return "\n".join(lines), title
        except Exception:
            return "", ""

    # ── Download ────────────────────────────────────────────────────

    async def _download(self, url: str, path: str = "", **kwargs) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # 推断文件名
        if not path:
            parsed = urllib.parse.urlparse(url)
            filename = Path(parsed.path).name or "download"
            path = filename

        dest = Path(path).expanduser().resolve()

        try:
            # 构造 Referer：从 URL 提取域名
            parsed = urllib.parse.urlparse(url)
            referer = f"{parsed.scheme}://{parsed.netloc}/"
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 TianshuBrowser/0.2"
                ),
                "Referer": referer,
                "Accept": "*/*",
            }
            async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers=headers) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))

                    dest.parent.mkdir(parents=True, exist_ok=True)
                    downloaded = 0
                    with open(dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)

            size_mb = downloaded / (1024 * 1024)
            return (
                f"✅ 已下载: {dest}\n"
                f"   大小: {downloaded:,} bytes ({size_mb:.1f} MB)\n"
                f"   来源: {url}"
            )
        except httpx.HTTPStatusError as e:
            return f"❌ HTTP {e.response.status_code}: {url}"
        except Exception as e:
            return f"❌ 下载失败: {type(e).__name__}: {e}"

    # ── Upload ──────────────────────────────────────────────────────

    async def _upload(
        self, local_path: str, url: str, method: str = "POST", field_name: str = "file", **kwargs
    ) -> str:
        src = Path(local_path).expanduser().resolve()
        if not src.exists():
            return f"❌ 文件不存在: {src}"
        if src.is_dir():
            return f"❌ 是目录不是文件: {src}"

        size = src.stat().st_size

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                if method.upper() == "PUT":
                    # PUT: 直接发送文件内容作为 body
                    with open(src, "rb") as f:
                        resp = await client.put(url, content=f.read())
                else:
                    # POST: multipart form upload
                    with open(src, "rb") as f:
                        files = {field_name: (src.name, f)}
                        resp = await client.post(url, files=files)

            body_preview = resp.text[:500]
            return (
                f"{'✅' if resp.status_code < 400 else '⚠️'} HTTP {resp.status_code}\n"
                f"   上传: {src.name} ({size:,} bytes)\n"
                f"   目标: {url}\n"
                f"   响应: {body_preview}"
            )
        except Exception as e:
            return f"❌ 上传失败: {type(e).__name__}: {e}"


def _fmt_browse_result(
    title: str, url: str, text: str, links: list, max_chars: int,
) -> str:
    """格式化浏览结果。"""
    text_display = text[:max_chars]
    truncated = len(text) > max_chars

    result = f"🌐 {title or '(无标题)'}\n"
    result += f"📍 {url}\n"
    result += f"📊 {len(text)} 字符 | {len(links)} 个链接\n"
    result += f"{'─' * 60}\n"
    result += text_display

    if truncated:
        result += f"\n{'─' * 60}\n… 省略了 {len(text) - max_chars} 字符"

    if links:
        result += f"\n{'─' * 60}\n🔗 链接 (前 20):\n"
        for i, (label, href) in enumerate(links[:20]):
            result += f"  [{i + 1}] {label[:60]}\n     {href}\n"

    return result


# ═══════════════════════════════════════════════════════════════════════════
# HTML 解析（零依赖——只用 stdlib）
# ═══════════════════════════════════════════════════════════════════════════

_SCRIPT_OR_STYLE = re.compile(
    r"<(script|style|noscript|iframe|svg|nav|footer|header|aside)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&[a-z]+;|&#\d+;")
_WHITESPACE = re.compile(r"\s{2,}")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_LINK = re.compile(
    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>',
    re.IGNORECASE,
)


def _extract_title(html: str) -> str:
    m = _TITLE.search(html)
    if m:
        return _clean_text(m.group(1)).strip()
    return ""


def _extract_text(html: str) -> str:
    # 去 script/style
    text = _SCRIPT_OR_STYLE.sub(" ", html)
    # 去标签
    text = _TAG.sub(" ", text)
    # 去 HTML 实体
    text = _ENTITY.sub(" ", text)
    # 合并空白
    text = _WHITESPACE.sub("\n", text)
    # 去空行
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return "\n".join(lines)


def _extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    links = []
    seen = set()
    for m in _LINK.finditer(html):
        href = m.group(1)
        label = _clean_text(m.group(2)).strip()
        if not label or label in seen:
            continue
        # 补全相对链接
        if not href.startswith(("http://", "https://")):
            href = urllib.parse.urljoin(base_url, href)
        seen.add(label)
        links.append((label, href))
    return links


def _clean_text(s: str) -> str:
    s = _TAG.sub(" ", s)
    s = _ENTITY.sub(" ", s)
    return _WHITESPACE.sub(" ", s)


# ── 实体解码表（常见）────────────────────────────────────────────
_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&apos;": "'", "&nbsp;": " ", "&#160;": " ",
    "&mdash;": "—", "&ndash;": "–", "&hellip;": "…",
    "&lsquo;": "'", "&rsquo;": "'", "&ldquo;": '"', "&rdquo;": '"',
    "&laquo;": "«", "&raquo;": "»",
}


def _entity_decode(s: str) -> str:
    for entity, char in _ENTITIES.items():
        s = s.replace(entity, char)
    return s
