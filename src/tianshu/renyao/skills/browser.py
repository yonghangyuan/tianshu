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

    async def _browse(self, url: str, max_chars: int = 8000) -> str:
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

        except httpx.HTTPStatusError as e:
            return f"❌ HTTP {e.response.status_code}: {url}"
        except httpx.TimeoutException:
            return f"❌ 超时: {url} (20s)"
        except Exception as e:
            return f"❌ 无法访问: {url}\n{type(e).__name__}: {e}"

        # 提取信息
        title = _extract_title(html)
        text = _extract_text(html)
        links = _extract_links(html, url)

        # 截断
        text_display = text[:max_chars]
        truncated = len(text) > max_chars

        result = f"🌐 {title or '(无标题)'}\n"
        result += f"📍 {resp.url}\n"
        result += f"📊 {len(text)} 字符 | {len(links)} 个链接\n"
        result += f"{'─' * 60}\n"
        result += text_display

        if truncated:
            result += f"\n{'─' * 60}\n… 省略了 {len(text) - max_chars} 字符"

        # 链接列表
        if links:
            result += f"\n{'─' * 60}\n🔗 链接 (前 20):\n"
            for i, (label, href) in enumerate(links[:20]):
                result += f"  [{i + 1}] {label[:60]}\n     {href}\n"

        return result

    # ── Download ────────────────────────────────────────────────────

    async def _download(self, url: str, path: str = "") -> str:
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
        self, local_path: str, url: str, method: str = "POST", field_name: str = "file"
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
