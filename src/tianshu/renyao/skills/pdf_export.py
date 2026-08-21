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

    # 临时 HTML 文件（file:// URI 传给浏览器）——立即关 fd，否则 Windows
    # 下打开的句柄会阻止 Edge 读写该文件
    fd, tmp_name = tempfile.mkstemp(suffix=".html", prefix="tianshu_pdf_")
    os.close(fd)
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
            # Linux/CI 环境必需：无沙箱运行（容器内 namespace 受限）+ 防 /dev/shm 过小崩
            "--no-sandbox", "--disable-dev-shm-usage",
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


def _rotate(path: str, angle: int, output: str = "") -> str:
    """旋转 PDF（90 的倍数，顺时针）。"""
    PdfReader, PdfWriter = _require_pypdf()
    if angle % 90 != 0:
        raise ValueError(f"旋转角度必须是 90 的倍数: {angle}")
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")
    reader = PdfReader(str(src))
    for page in reader.pages:
        page.rotate(angle)
    writer = PdfWriter()
    writer.append(reader)
    out = _resolve_output(output, src) if output else src.with_name(f"{src.stem}_rot{angle}.pdf")
    with open(out, "wb") as f:
        writer.write(f)
    return f"✅ 旋转 {angle}° → {out} ({len(reader.pages)} 页)"


def _extract_text(path: str, page: int = 0, output: str = "") -> str:
    """提取 PDF 文本。page=0 → 全部；否则第 N 页。超长自动落盘 .txt。"""
    PdfReader, _ = _require_pypdf()
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")
    reader = PdfReader(str(src))
    if page > 0:
        if page > len(reader.pages):
            raise ValueError(f"页码超界: {page} > {len(reader.pages)}")
        return reader.pages[page - 1].extract_text() or ""
    text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
    if len(text) <= 8000:
        return text
    out = Path(output).expanduser() if output else src.with_suffix(".txt")
    out.write_text(text, encoding="utf-8")
    return f"文本过长 ({len(text)} 字)，已保存到 {out}"


def _extract_images(path: str, output_dir: str = "") -> str:
    """提取 PDF 内嵌图片到目录。"""
    PdfReader, _ = _require_pypdf()
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")
    reader = PdfReader(str(src))
    out_dir = Path(output_dir).expanduser() if output_dir else src.parent / f"{src.stem}_images"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for i, page in enumerate(reader.pages, 1):
        for img in page.images:
            name = img.name or f"p{i}_{saved}"
            if "." not in Path(name).name:
                name = f"{name}.png"
            target = out_dir / Path(name).name
            with open(target, "wb") as f:
                f.write(img.data)
            saved += 1
    return f"✅ 提取 {saved} 张图片 → {out_dir}"


def _watermark(path: str, watermark_text: str, output: str = "") -> str:
    """加水印：Edge 渲染水印页（按目标页尺寸逐页生成）→ pypdf 合并。"""
    PdfReader, PdfWriter = _require_pypdf()
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")
    reader = PdfReader(str(src))
    # 页尺寸取最大值（混尺寸 PDF 用统一页面，水印仍可读）；
    # CSS pt = PDF pt = 1/72 inch，margin 0 时每 div 恰好一页
    w = max(float(p.mediabox.width) for p in reader.pages)
    h = max(float(p.mediabox.height) for p in reader.pages)
    esc = _html_mod.escape(watermark_text)
    pages_html = "\n".join(
        f'<div class="page"><span class="wm">{esc}</span></div>' for _ in reader.pages
    )
    html_doc = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>\n"
        f"@page {{ size: {w:.0f}pt {h:.0f}pt; margin: 0; }}\n"
        "html, body { margin: 0; padding: 0; }\n"
        f".page {{ width: {w:.0f}pt; height: {h:.0f}pt; position: relative;"
        " overflow: hidden; break-after: page; }\n"
        ".page:last-child { break-after: auto; }\n"
        ".wm { position: absolute; top: 40%; left: -15%; width: 130%; text-align: center;"
        " transform: rotate(-35deg); font-size: 34pt; color: rgba(110,110,110,0.25);"
        " font-family: 'Microsoft YaHei', sans-serif; white-space: nowrap; }\n"
        "</style></head><body>\n"
        f"{pages_html}\n</body></html>"
    )

    import tempfile
    fd, wm_name = tempfile.mkstemp(suffix=".pdf", prefix="tianshu_wm_")
    os.close(fd)  # 见 _html_doc_to_pdf: 打开的句柄会阻止 Edge 写入
    wm_pdf = Path(wm_name)
    try:
        _html_doc_to_pdf(html_doc, wm_pdf)
        wm_reader = PdfReader(str(wm_pdf))
        if len(wm_reader.pages) < len(reader.pages):
            raise RuntimeError(f"水印页数不匹配: {len(wm_reader.pages)} < {len(reader.pages)}")
        # 先 append 到 writer 再 merge——直接在 reader 页上 merge 会触发
        # pypdf 弃用警告 (replace_contents 对非 writer 页不可靠)
        writer = PdfWriter()
        writer.append(reader)
        for page, wm_page in zip(writer.pages, wm_reader.pages):
            page.merge_page(wm_page)
        out = _resolve_output(output, src) if output else src.with_name(f"{src.stem}_wm.pdf")
        with open(out, "wb") as f:
            writer.write(f)
        return f"✅ 水印 '{watermark_text}' → {out} ({len(writer.pages)} 页)"
    finally:
        try:
            wm_pdf.unlink(missing_ok=True)
        except PermissionError:
            pass


def _encrypt(path: str, password: str, output: str = "") -> str:
    PdfReader, PdfWriter = _require_pypdf()
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")
    reader = PdfReader(str(src))
    writer = PdfWriter()
    writer.append(reader)
    writer.encrypt(password, algorithm="AES-256-R5")
    out = _resolve_output(output, src) if output else src.with_name(f"{src.stem}_enc.pdf")
    with open(out, "wb") as f:
        writer.write(f)
    return f"✅ 已加密 (AES-256) → {out}"


def _decrypt(path: str, password: str, output: str = "") -> str:
    PdfReader, PdfWriter = _require_pypdf()
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")
    reader = PdfReader(str(src))
    if reader.is_encrypted:
        result = reader.decrypt(password)
        if result == 0:
            raise ValueError("密码错误")
    writer = PdfWriter()
    writer.append(reader)
    out = _resolve_output(output, src) if output else src.with_name(f"{src.stem}_dec.pdf")
    with open(out, "wb") as f:
        writer.write(f)
    return f"✅ 已解密 → {out}"


def _form_fields(path: str) -> str:
    PdfReader, _ = _require_pypdf()
    reader = PdfReader(str(Path(path).expanduser()))
    fields = reader.get_fields() or {}
    if not fields:
        return "该 PDF 无可填写的表单字段"
    lines = [f"共 {len(fields)} 个表单字段:"]
    for name, f in fields.items():
        ftype = (f or {}).get("/FT", "?")
        fval = (f or {}).get("/V", "")
        lines.append(f"- {name} ({ftype}) = {fval}")
    return "\n".join(lines)


def _fill_form(path: str, fields: str, output: str = "") -> str:
    """填充 AcroForm。fields: JSON 字符串 {"字段名": "值", ...}"""
    import json
    PdfReader, PdfWriter = _require_pypdf()
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"PDF 不存在: {path}")
    values = json.loads(fields)
    if not isinstance(values, dict):
        raise ValueError("fields 必须是 JSON 对象")
    reader = PdfReader(str(src))
    existing = reader.get_fields() or {}
    filled = 0
    for name, val in values.items():
        if name not in existing:
            continue
        try:
            reader.update_page_form_field_values(reader.pages[0], {name: val})
            filled += 1
        except Exception:
            continue
    writer = PdfWriter()
    writer.append(reader)
    out = _resolve_output(output, src) if output else src.with_name(f"{src.stem}_filled.pdf")
    with open(out, "wb") as f:
        writer.write(f)
    missing = [k for k in values if k not in existing]
    msg = f"✅ 填充 {filled}/{len(values)} 字段 → {out}"
    if missing:
        msg += f"\n⚠ 字段不存在: {', '.join(missing)}"
    return msg


class PDFExportSkill(BaseSkill):
    name = "pdf-export"
    description = (
        "PDF 生成与工具箱: Markdown/HTML → PDF（Edge 渲染），"
        "合并/拆分/旋转/水印/加密/提取/表单（对标 Hermes pdf skill）"
    )
    trigram = "地"
    trigger_keywords = [
        "生成pdf", "导出pdf", "转pdf", "md转pdf", "markdown转pdf",
        "html转pdf", "合并pdf", "拆分pdf", "pdf信息", "pdf页数",
        "旋转pdf", "pdf文本", "提取图片", "水印", "加密pdf", "解密pdf",
        "pdf表单", "填写表单",
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
            SkillTool(
                name="pdf_rotate",
                description="旋转 PDF（90 的倍数，顺时针）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "PDF 文件路径"},
                        "angle": {"type": "integer", "description": "旋转角度: 90/180/270"},
                        "output": {"type": "string", "description": "输出路径，默认 <原名>_rot<角度>.pdf"},
                    },
                    "required": ["path", "angle"],
                },
                handler=_rotate,
                permission_level=2,
            ),
            SkillTool(
                name="pdf_extract_text",
                description=(
                    "提取 PDF 文本。page=0 提取全部（超 8000 字自动落盘 .txt），"
                    "page>0 提取指定页。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "PDF 文件路径"},
                        "page": {"type": "integer", "description": "页码（1 起），默认 0=全部"},
                        "output": {"type": "string", "description": "文本过长时保存的 .txt 路径"},
                    },
                    "required": ["path"],
                },
                handler=_extract_text,
                permission_level=0,
            ),
            SkillTool(
                name="pdf_extract_images",
                description="提取 PDF 内嵌图片到目录。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "PDF 文件路径"},
                        "output_dir": {"type": "string", "description": "输出目录，默认 <原名>_images/"},
                    },
                    "required": ["path"],
                },
                handler=_extract_images,
                permission_level=2,
            ),
            SkillTool(
                name="pdf_watermark",
                description=(
                    "给 PDF 每页加斜向文字水印（按原页尺寸渲染，浅灰低透明度）。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "PDF 文件路径"},
                        "watermark_text": {"type": "string", "description": "水印文字，如 机密/内部资料"},
                        "output": {"type": "string", "description": "输出路径，默认 <原名>_wm.pdf"},
                    },
                    "required": ["path", "watermark_text"],
                },
                handler=_watermark,
                permission_level=2,
            ),
            SkillTool(
                name="pdf_encrypt",
                description="给 PDF 加打开密码（AES-256）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "PDF 文件路径"},
                        "password": {"type": "string", "description": "打开密码"},
                        "output": {"type": "string", "description": "输出路径，默认 <原名>_enc.pdf"},
                    },
                    "required": ["path", "password"],
                },
                handler=_encrypt,
                permission_level=2,
            ),
            SkillTool(
                name="pdf_decrypt",
                description="用密码解除 PDF 加密。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "加密 PDF 文件路径"},
                        "password": {"type": "string", "description": "打开密码"},
                        "output": {"type": "string", "description": "输出路径，默认 <原名>_dec.pdf"},
                    },
                    "required": ["path", "password"],
                },
                handler=_decrypt,
                permission_level=2,
            ),
            SkillTool(
                name="pdf_form_fields",
                description="列出 PDF 的 AcroForm 可填写字段（名称/类型/当前值）。",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "PDF 文件路径"},
                    },
                    "required": ["path"],
                },
                handler=_form_fields,
                permission_level=0,
            ),
            SkillTool(
                name="pdf_fill_form",
                description=(
                    "填充 PDF 表单。fields 为 JSON 字符串如 "
                    "{\"姓名\": \"张三\", \"日期\": \"2026-08-13\"}。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "含表单的 PDF 文件路径"},
                        "fields": {"type": "string", "description": "JSON 字符串: 字段名 → 值"},
                        "output": {"type": "string", "description": "输出路径，默认 <原名>_filled.pdf"},
                    },
                    "required": ["path", "fields"],
                },
                handler=_fill_form,
                permission_level=2,
            ),
        ]
