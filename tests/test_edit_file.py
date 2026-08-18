"""edit_file 行级编辑工具测试。"""

import asyncio
import pytest

from tianshu.renyao.skills.file_ops import FileOpsSkill


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def skill():
    return FileOpsSkill()


def test_edit_single_match(skill, tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x = 1\ny = 2\n", encoding="utf-8")
    out = _run(skill._edit_file(str(p), "x = 1", "x = 42"))
    assert "✅ 已替换 1 处" in out
    assert "--- Diff ---" in out
    assert p.read_text(encoding="utf-8") == "x = 42\ny = 2\n"


def test_edit_no_match(skill, tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x = 1\n", encoding="utf-8")
    out = _run(skill._edit_file(str(p), "z = 9", "z = 10"))
    assert "❌ 未找到目标文本" in out
    assert p.read_text(encoding="utf-8") == "x = 1\n"


def test_edit_multiple_matches_requires_replace_all(skill, tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x = 1\nx = 1\n", encoding="utf-8")
    out = _run(skill._edit_file(str(p), "x = 1", "x = 2"))
    assert "❌ 找到 2 处匹配" in out
    assert p.read_text(encoding="utf-8") == "x = 1\nx = 1\n"


def test_edit_replace_all(skill, tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x = 1\nx = 1\n", encoding="utf-8")
    out = _run(skill._edit_file(str(p), "x = 1", "x = 2", replace_all=True))
    assert "✅ 已替换 2 处" in out
    assert p.read_text(encoding="utf-8") == "x = 2\nx = 2\n"


def test_edit_gbk_file(skill, tmp_path):
    p = tmp_path / "b.txt"
    p.write_bytes("你好世界\n".encode("gbk"))
    out = _run(skill._edit_file(str(p), "你好", "再见"))
    assert "✅ 已替换 1 处" in out
    assert p.read_bytes() == "再见世界\n".encode("gbk")


def test_edit_missing_file(skill, tmp_path):
    out = _run(skill._edit_file(str(tmp_path / "nope.py"), "a", "b"))
    assert "❌ 文件不存在" in out


def test_edit_new_string_contains_old(skill, tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x = 1\n", encoding="utf-8")
    out = _run(skill._edit_file(str(p), "x = 1", "x = x + 1"))
    assert "✅ 已替换 1 处" in out
    assert p.read_text(encoding="utf-8") == "x = x + 1\n"


def test_edit_preserves_crlf(skill, tmp_path):
    p = tmp_path / "win.txt"
    p.write_bytes(b"line1\r\nline2\r\n")
    out = _run(skill._edit_file(str(p), "line1", "LINE1"))
    assert "✅ 已替换 1 处" in out
    assert p.read_bytes() == b"LINE1\r\nline2\r\n"


def test_edit_preserves_lf(skill, tmp_path):
    p = tmp_path / "unix.txt"
    p.write_bytes(b"line1\nline2\n")
    out = _run(skill._edit_file(str(p), "line2", "LINE2"))
    assert "✅ 已替换 1 处" in out
    assert p.read_bytes() == b"line1\nLINE2\n"


def test_edit_unicode_roundtrip(skill, tmp_path):
    p = tmp_path / "c.py"
    p.write_text("# 注释\nvalue = \"🎯\"\n", encoding="utf-8")
    out = _run(skill._edit_file(str(p), "\"🎯\"", "\"新值\""))
    assert "✅ 已替换 1 处" in out
    assert p.read_text(encoding="utf-8") == "# 注释\nvalue = \"新值\"\n"
