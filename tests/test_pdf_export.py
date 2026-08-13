"""PDF 导出测试 — 模板转换 + Edge headless 真实渲染 + pypdf 工具箱。

Edge 渲染测试在本机有 Edge 时执行（Windows 自带），无则跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.renyao.skills.pdf_export import (
    PDFExportSkill,
    _decrypt,
    _encrypt,
    _extract_images,
    _extract_text,
    _fill_form,
    _find_browser,
    _form_fields,
    _html_doc_to_pdf,
    _inject_css,
    _info,
    _merge,
    _rotate,
    _split,
    _watermark,
    _wrap_html,
    html_to_pdf_file,
    md_to_html,
    md_to_pdf_file,
)

_EDGE = _find_browser()
needs_edge = pytest.mark.skipif(not _EDGE, reason="本机无 Edge/Chrome")


# ── 模板转换 ──────────────────────────────────────────────────────────────

class TestHtmlTemplate:
    def test_md_renders_table_and_code(self):
        html = md_to_html(
            "# 标题\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint(1)\n```"
        )
        assert "<h1>标题</h1>" in html
        assert "<table>" in html
        assert "<pre>" in html

    def test_md_preserves_chinese(self):
        html = md_to_html("天枢三爻架构：天、人、地。")
        assert "天枢三爻架构" in html

    def test_wrap_html_css_and_title_escape(self):
        doc = _wrap_html("<p>内容</p>", "标题 <x>")
        assert "@page" in doc
        assert "A4" in doc
        assert "&lt;x&gt;" in doc  # title 转义

    def test_inject_css_into_full_doc(self):
        doc = "<html><head><meta charset='utf-8'></head><body><h1>Hi</h1></body></html>"
        out = _inject_css(doc)
        assert "@page" in out
        assert out.index("<style") < out.index("<h1>")

    def test_inject_css_keeps_existing_style(self):
        doc = "<html><head><style>body{}</style></head><body>x</body></html>"
        out = _inject_css(doc)
        assert out.count("<style") == 1

    def test_md_to_pdf_empty_raises(self, tmp_path):
        with pytest.raises(ValueError):
            md_to_pdf_file("   ", tmp_path / "a.pdf")


# ── Edge 真实渲染 ─────────────────────────────────────────────────────────

@needs_edge
class TestEdgePdf:
    def test_md_to_pdf_end_to_end(self, tmp_path):
        out = md_to_pdf_file(
            "# 测试文档\n\n天枢 PDF 导出链路验证。TESTMARK-42\n\n"
            "- 列表项一\n- 列表项二\n",
            tmp_path / "测试文档.pdf",
            title="测试",
        )
        assert out.exists()
        assert out.stat().st_size > 500
        assert _info(str(out)).startswith(f"{out}: 1 页")

    def test_html_to_pdf_full_doc(self, tmp_path):
        html = (
            "<html><head><meta charset='utf-8'></head>"
            "<body><h1>Hello PDF</h1><p>TESTMARK-HTML-7</p></body></html>"
        )
        out = html_to_pdf_file(html, tmp_path / "b.pdf")
        assert out.exists() and out.stat().st_size > 500

    def test_html_to_pdf_fragment(self, tmp_path):
        out = html_to_pdf_file("<h1>片段</h1><p>自动套模板。</p>", tmp_path / "c.pdf")
        assert out.exists() and out.stat().st_size > 500

    def test_multi_page_document(self, tmp_path):
        md = "\n\n".join(f"# 第{i}章\n\n" + "段落内容。" * 60 for i in range(1, 8))
        out = md_to_pdf_file(md, tmp_path / "long.pdf", title="长文档")
        from pypdf import PdfReader
        reader = PdfReader(str(out))
        assert len(reader.pages) >= 2  # 7 章 × 300 字 ≈ 2 页 A4

    def test_empty_body_yields_blank_page(self, tmp_path):
        # Edge 对空 body 渲染出 1 页空白 PDF（合法输出）——验证产物仍有效
        out = _html_doc_to_pdf("<html><body></body></html>", tmp_path / "empty.pdf")
        assert out.exists() and out.stat().st_size >= 100

    def test_missing_browser_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tianshu.renyao.skills.pdf_export._find_browser", lambda: "")
        with pytest.raises(RuntimeError, match="未找到 Edge"):
            _html_doc_to_pdf("<p>x</p>", tmp_path / "no.pdf")


# ── pypdf 工具箱 ───────────────────────────────────────────────────────────

@pytest.fixture
def two_pdfs(tmp_path):
    """生成两个真实 PDF 供 merge/split 测试。"""
    a = md_to_pdf_file("# A\n\n第一份。", tmp_path / "a.pdf", title="A")
    b = md_to_pdf_file("# B\n\n第二份。", tmp_path / "b.pdf", title="B")
    return a, b


@needs_edge
class TestPdfToolbox:
    def test_merge(self, tmp_path, two_pdfs):
        a, b = two_pdfs
        out = tmp_path / "merged.pdf"
        msg = _merge([str(a), str(b)], str(out))
        assert out.exists()
        assert "2 页" in msg

    def test_split(self, tmp_path, two_pdfs):
        a, _ = two_pdfs
        out_dir = tmp_path / "pages"
        msg = _split(str(a), str(out_dir))
        assert "1 页" in msg
        assert (out_dir / "a_p1.pdf").exists()

    def test_info_pages(self, two_pdfs):
        a, _ = two_pdfs
        msg = _info(str(a))
        assert "1 页" in msg

    def test_merge_missing_file_raises(self, tmp_path, two_pdfs):
        a, _ = two_pdfs
        with pytest.raises(FileNotFoundError):
            _merge([str(a), str(tmp_path / "不存在.pdf")], str(tmp_path / "x.pdf"))


# ── 工具箱扩展: 旋转/提取/水印/加密/表单 ───────────────────────────────────

@needs_edge
class TestPdfToolboxExtended:
    def test_rotate(self, two_pdfs):
        a, _ = two_pdfs
        msg = _rotate(str(a), 90)
        assert "旋转 90°" in msg
        from pypdf import PdfReader
        reader = PdfReader(str(a).replace(".pdf", "_rot90.pdf"))
        assert len(reader.pages) == 1

    def test_rotate_bad_angle(self, two_pdfs):
        a, _ = two_pdfs
        with pytest.raises(ValueError):
            _rotate(str(a), 45)

    def test_extract_text(self, two_pdfs):
        a, _ = two_pdfs
        text = _extract_text(str(a))
        assert "# A" in text or "第一份" in text

    def test_extract_text_single_page(self, two_pdfs):
        a, _ = two_pdfs
        text = _extract_text(str(a), page=1)
        assert len(text) > 0

    def test_extract_text_page_out_of_range(self, two_pdfs):
        a, _ = two_pdfs
        with pytest.raises(ValueError):
            _extract_text(str(a), page=99)

    def test_extract_images_no_images(self, two_pdfs, tmp_path):
        a, _ = two_pdfs
        msg = _extract_images(str(a), str(tmp_path / "imgs"))
        assert "0 张" in msg

    def test_watermark(self, two_pdfs, tmp_path):
        a, _ = two_pdfs
        out = tmp_path / "wm.pdf"
        msg = _watermark(str(a), "机密", str(out))
        assert out.exists()
        assert "1 页" in msg
        from pypdf import PdfReader
        assert len(PdfReader(str(out)).pages) == 1
        # 水印合并后文本仍可提取（内容未被破坏）
        text = "".join((p.extract_text() or "") for p in PdfReader(str(out)).pages)
        assert "第一份" in text or "机密" in text

    def test_watermark_multipage(self, tmp_path):
        md = "\n\n".join(f"# 第{i}章\n\n段落内容。" * 30 for i in range(1, 6))
        src = md_to_pdf_file(md, tmp_path / "multi.pdf")
        from pypdf import PdfReader
        n = len(PdfReader(str(src)).pages)
        out = tmp_path / "multi_wm.pdf"
        _watermark(str(src), "内部资料", str(out))
        assert len(PdfReader(str(out)).pages) == n

    def test_encrypt_decrypt_roundtrip(self, two_pdfs, tmp_path):
        a, _ = two_pdfs
        enc = tmp_path / "enc.pdf"
        _encrypt(str(a), "secret123", str(enc))
        from pypdf import PdfReader
        reader = PdfReader(str(enc))
        assert reader.is_encrypted
        # 解密
        dec = tmp_path / "dec.pdf"
        _decrypt(str(enc), "secret123", str(dec))
        dec_reader = PdfReader(str(dec))
        assert not dec_reader.is_encrypted
        assert "第一份" in dec_reader.pages[0].extract_text()

    def test_decrypt_wrong_password(self, two_pdfs, tmp_path):
        a, _ = two_pdfs
        enc = tmp_path / "enc2.pdf"
        _encrypt(str(a), "secret123", str(enc))
        with pytest.raises(ValueError):
            _decrypt(str(enc), "wrong", str(tmp_path / "x.pdf"))

    def test_form_fields_empty_on_plain_pdf(self, two_pdfs):
        a, _ = two_pdfs
        msg = _form_fields(str(a))
        assert "无可填写" in msg

    def test_fill_form_no_fields(self, two_pdfs, tmp_path):
        a, _ = two_pdfs
        msg = _fill_form(str(a), '{"姓名": "张三"}', str(tmp_path / "f.pdf"))
        assert "0/1" in msg
        assert "姓名" in msg  # 报告不存在的字段


# ── Skill 注册 ────────────────────────────────────────────────────────────

class TestPdfSkill:
    def test_tools_registered(self):
        names = [t.name for t in PDFExportSkill().get_tools()]
        assert names == [
            "md_to_pdf", "html_to_pdf", "pdf_merge", "pdf_split", "pdf_info",
            "pdf_rotate", "pdf_extract_text", "pdf_extract_images",
            "pdf_watermark", "pdf_encrypt", "pdf_decrypt",
            "pdf_form_fields", "pdf_fill_form",
        ]

    def test_loader_includes_pdf_export(self):
        from tianshu.renyao.skills.loader import SkillLoader
        loader = SkillLoader()
        loader.load_builtins()
        assert "pdf-export" in loader._skills
