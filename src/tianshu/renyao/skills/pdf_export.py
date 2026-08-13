"""PDF 导出 Skill — Markdown/HTML → PDF + pypdf 工具箱。

方案: Edge headless `--print-to-pdf`（与 browser skill 同一引擎，零外部下载）。
  - 创建 PDF: Markdown/HTML → 打印模板 (A4 CSS) → Edge 渲染（CJK 走系统字体）
  - 工具箱: pypdf 惰性导入 — 合并/拆分/信息（对标 Hermes pdf skill 的核心操作）
  - 跳过的 Hermes 路线: reportlab 手工排版（中文效果差）、qpdf/poppler（外部工具）

三爻分类: 地（执行/输出）
"""

from __future__ import annotations

import html as _html_mod
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from .base import BaseSkill, SkillTool

_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

_PRINT_CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
  font-size: 11pt; line-height: 1.7; color: #1a1a1a; max-width: 100%;
}
h1, h2, h3, h4 { page-break-after: avoid; color: #10233f; }
h1 { font-size: 20pt; border-bottom: 2px solid #1a4d8f; padding-bottom: 6px; }
h2 { font-size: 15pt; border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 1.4em; }
h3 { font-size: 12.5pt; }
p { margin: 0.6em 0; }
pre {
  background: #f5f6f8; padding: 10px 12px; border-radius: 4px;
  font-size: 9.5pt; line-height: 1.5; white-space: pre-wrap; word-break: break-all;
  page-break-inside: avoid;
}
code { font-family: Consolas, "Courier New", monospace; background: #f0f1f3; padding: 1px 4px; border-radius: 2px; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 0.8em 0; page-break-inside: avoid; }
th, td { border: 1px solid #d5d8dc; padding: 6px 10px; text-align: left; }
th { background: #f2f4f7; }
img { max-width: 100%; }
blockquote { border-left: 3px solid #b9c3cf; margin: 0.8em 0; padding: 2px 12px; color: #555; }
ul, ol { padding-left: 1.6em; }
li { margin: 0.25em 0; }
hr { border: none; border-top: 1px solid #ddd; margin: 1.2em 0; }
a { color: #1a4d8f; text-decoration: none; }
""".strip()


# ── 核心引擎 ──────────────────────────────────────────────────────────────

def _find_browser() -> str:
    """探测 Edge/Chrome 可执行文件路径。"""
    exe = shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")
    if exe:
        return exe
    for p in _BROWSER_CANDIDATES:
        if Path(p).exists():
            return p
    return ""


def md_to_html(md_text: str) -> str:
    """Markdown → HTML body（表格/围栏代码/列表扩展）。"""
    try:
        import markdown as _md
    except ImportError as e:
        raise RuntimeError("需要 markdown 库: pip install 'tianshu[pdf]'") from e
    return _md.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])


def _wrap_html(body: str, title: str = "") -> str:
    """把 HTML body 包进带打印 CSS 的完整文档。"""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_html_mod.escape(title)}</title>\n"
        f"<style>\n{_PRINT_CSS}\n</style>\n</head>\n"
        f"<body>\n{body}\n</body>\n</html>"
    )


def _inject_css(full_html: str) -> str:
    """完整 HTML 文档 → 注入打印 CSS（已有 <style> 则不动）。"""
    if "<style" in full_html.lower():
        return full_html
    lower = full_html.lower()
    idx = lower.find("</head>")
    if idx > 0:
        return full_html[:idx] + f"<style>\n{_PRINT_CSS}\n</style>\n" + full_html[idx:]
    return _wrap_html(full_html)  # 无 head → 当作 body 处理


def _html_doc_to_pdf(html_doc: str, pdf_path: Path) -> Path:
    """HTML 文档 → PDF。核心: Edge headless print-to-pdf，零外部下载。"""
    exe = _find_browser()
    if not exe:
        raise RuntimeError(
            "未找到 Edge/Chrome — Windows 自带 Edge，"
            "如被卸载请安装或设置 TIANSHU_PDF_BROWSER 环境变量"
        )
    pdf_path = pdf_path.expanduser().resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # 临时 HTML 文件（file:// URI 传给浏览器）
    fd, tmp_name = tempfile.mkstemp(suffix=".html", prefix="tianshu_pdf_")
    tmp_html = Path(tmp_name)
    # 每次调用独立 profile 目录——共用目录时连发的 Edge 实例会挂到
    # 第一个进程上（SingletonLock），不渲染直接退出
    user_data = Path(tempfile.gettempdir()) / (
        f"tianshu_edge_pdf_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp_html.write_text(html_doc, encoding="utf-8")
        cmd = [
            exe,
            "--headless=new", "--no-first-run", "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={user_data}",
            f"--print-to-pdf={pdf_path}",
            tmp_html.as_uri(),
        ]
        proc = subprocess.run(
            cmd, timeout=90,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Edge 渲染失败 (exit={proc.returncode})")
    finally:
        # Windows 下 Edge 退出后可能仍短暂占用文件 → 忽略删除失败
        try:
            tmp_html.unlink(missing_ok=True)
        except PermissionError:
            pass
        shutil.rmtree(user_data, ignore_errors=True)

    if not pdf_path.exists() or pdf_path.stat().st_size < 100:
        raise RuntimeError("PDF 生成失败: 输出文件为空（HTML 内容可能无效）")
    return pdf_path


def _verify(pdf_path: Path) -> str:
    """验证产物并返回可读描述。pypdf 可用时报告页数。"""
    try:
        from pypdf import PdfReader
        n = len(PdfReader(str(pdf_path)).pages)
        return f"{pdf_path} ({n} 页, {pdf_path.stat().st_size // 1024} KB)"
    except Exception:
        return f"{pdf_path} ({pdf_path.stat().st_size} bytes)"


def md_to_pdf_file(md_text: str, pdf_path: str | Path, title: str = "") -> Path:
    """同步核心: Markdown 文本 → PDF 文件。"""
    if not md_text.strip():
        raise ValueError("Markdown 内容为空")
    doc = _wrap_html(md_to_html(md_text), title=title)
    return _html_doc_to_pdf(doc, Path(pdf_path))


def html_to_pdf_file(html_text: str, pdf_path: str | Path) -> Path:
    """同步核心: HTML（片段或完整文档）→ PDF 文件。"""
    if not html_text.strip():
        raise ValueError("HTML 内容为空")
    if "<html" in html_text.lower():
        doc = _inject_css(html_text)
    else:
        doc = _wrap_html(html_text)
    return _html_doc_to_pdf(doc, Path(pdf_path))


def _require_pypdf():
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as e:
        raise RuntimeError("需要 pypdf 库: pip install 'tianshu[pdf]'") from e
    return PdfReader, PdfWriter


# ── 工具 handlers ─────────────────────────────────────────────────────────

def _resolve_output(output: str, source_path: Path | None) -> Path:
    if output:
        p = Path(output).expanduser()
        return p if p.suffix.lower() == ".pdf" else p.with_suffix(".pdf")
    if source_path is not None:
        return source_path.with_suffix(".pdf")
    return Path("output.pdf")


def _md_to_pdf(source: str, output: str = "", title: str = "") -> str:
    """source: Markdown 内容或 .md 文件路径 → PDF。"""
    src = Path(source).expanduser() if source.strip() else None
    if src is not None and src.exists() and src.suffix.lower() in (".md", ".markdown", ".txt"):
        md_text = src.read_text(encoding="utf-8", errors="replace")
        src_path = src
    else:
        md_text = source
        src_path = None
    if not title:
        title = src_path.stem if src_path else ""
    out = md_to_pdf_file(md_text, _resolve_output(output, src_path), title=title)
    return f"✅ {_verify(out)}"


def _html_to_pdf(source: str, output: str = "") -> str:
    """source: HTML 内容或 .html 文件路径 → PDF。"""
    src = Path(source).expanduser() if source.strip() else None
    if src is not None and src.exists() and src.suffix.lower() in (".html", ".htm"):
        html_text = src.read_text(encoding="utf-8", errors="replace")
    else:
        html_text = source
        src = None
    out = html_to_pdf_file(html_text, _resolve_output(output, src))
    return f"✅ {_verify(out)}"


def _merge(paths: list[str], output: str) -> str:
    PdfReader, PdfWriter = _require_pypdf()
    writer = PdfWriter()
    n = 0
    for p in paths:
        pp = Path(p).expanduser()
        if not pp.exists():
            raise FileNotFoundError(f"PDF 不存在: {p}")
        for page in PdfReader(str(pp)).pages:
            writer.add_page(page)
            n += 1
    out = _resolve_output(output, None)
    with open(out, "wb") as f:
        writer.write(f)
    return f"✅ 合并 {len(paths)} 个文件 → {out} ({n} 页)"


def _split(path: str, output_dir: str = "") -> str:
    PdfReader, PdfWriter = _require_pypdf()
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")
    reader = PdfReader(str(src))
    out_dir = Path(output_dir).expanduser() if output_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, page in enumerate(reader.pages, 1):
        w = PdfWriter()
        w.add_page(page)
        with open(out_dir / f"{src.stem}_p{i}.pdf", "wb") as f:
            w.write(f)
    return f"✅ 拆分 {len(reader.pages)} 页 → {out_dir}/{src.stem}_p*.pdf"


def _info(path: str) -> str:
    PdfReader, _ = _require_pypdf()
    reader = PdfReader(str(Path(path).expanduser()))
    lines = [f"{path}: {len(reader.pages)} 页"]
    meta = reader.metadata or {}
    if meta.get("/Title"):
        lines.append(f"标题: {meta['/Title']}")
    lines.append(f"加密: {'是' if reader.is_encrypted else '否'}")
    return "\n".join(lines)


class PDFExportSkill(BaseSkill):
    name = "pdf-export"
    description = "PDF 生成与工具箱: Markdown/HTML → PDF（Edge 渲染），合并/拆分/信息"
    trigram = "地"
    trigger_keywords = [
        "生成pdf", "导出pdf", "转pdf", "md转pdf", "markdown转pdf",
        "html转pdf", "合并pdf", "拆分pdf", "pdf信息", "pdf页数",
    ]

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="md_to_pdf",
                description=(
                    "把 Markdown 内容（或 .md 文件路径）转成 PDF。"
                    "使用本机 Edge 渲染，中英文混排质量好，A4 排版。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Markdown 内容或 .md/.txt 文件路径"},
                        "output": {"type": "string", "description": "输出 PDF 路径，默认与源文件同名"},
                        "title": {"type": "string", "description": "文档标题（PDF 元信息），默认取文件名"},
                    },
                    "required": ["source"],
                },
                handler=_md_to_pdf,
                permission_level=2,
            ),
            SkillTool(
                name="html_to_pdf",
                description=(
                    "把 HTML 内容（或 .html 文件路径）转成 PDF。"
                    "片段自动套用 A4 打印模板；完整文档自动注入打印 CSS。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "HTML 内容或 .html/.htm 文件路径"},
                        "output": {"type": "string", "description": "输出 PDF 路径，默认与源文件同名"},
                    },
                    "required": ["source"],
                },
                handler=_html_to_pdf,
                permission_level=2,
            ),
            SkillTool(
                name="pdf_merge",
                description="把多个 PDF 文件按顺序合并为一个。",
                parameters={
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "PDF 文件路径列表，按合并顺序"},
                        "output": {"type": "string", "description": "输出 PDF 路径（必填）"},
                    },
                    "required": ["paths", "output"],
                },
                handler=_merge,
                permission_level=2,
            ),
            SkillTool(
                name="pdf_split",
                description="把一个 PDF 拆成单页文件（<原名>_p1.pdf, _p2.pdf ...）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "PDF 文件路径"},
                        "output_dir": {"type": "string", "description": "输出目录，默认与源文件同目录"},
                    },
                    "required": ["path"],
                },
                handler=_split,
                permission_level=2,
            ),
            SkillTool(
                name="pdf_info",
                description="查看 PDF 页数、标题、是否加密。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "PDF 文件路径"},
                    },
                    "required": ["path"],
                },
                handler=_info,
                permission_level=0,
            ),
        ]
