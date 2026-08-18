"""PTC 代码模式测试 — 真实 Python 子进程 + mock 工具执行器。"""

import asyncio
import time

import pytest

from tianshu.core.ptc import run_program


class _Recorder:
    """记录调用并返回确定结果的 exec_tool。"""
    def __init__(self, raise_for: dict[str, Exception] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.raise_for = raise_for or {}

    async def __call__(self, name, args):
        self.calls.append((name, args))
        if name in self.raise_for:
            raise self.raise_for[name]
        return f"result-of-{name}"


@pytest.mark.asyncio
async def test_tools_run_and_submit():
    rec = _Recorder()
    code = '''
r1 = tools.run("read_file", {"path": "a.py"})
r2 = tools.run("list_dir", {"path": "."})
submit(r1 + "|" + r2)
'''
    out = await run_program(code, rec, timeout=30)
    assert rec.calls == [("read_file", {"path": "a.py"}), ("list_dir", {"path": "."})]
    assert out == "result-of-read_file|result-of-list_dir"


@pytest.mark.asyncio
async def test_no_submit_returns_stdout_tail():
    rec = _Recorder()
    code = '''
for i in range(5):
    print(f"line-{i}")
print("TAIL-MARKER")
'''
    out = await run_program(code, rec, timeout=30)
    assert "TAIL-MARKER" in out
    assert "line-0" in out


@pytest.mark.asyncio
async def test_exec_tool_error_surfaces_as_runtime_error():
    rec = _Recorder(raise_for={"boom": RuntimeError("炸了")})
    code = '''
try:
    tools.run("boom", {})
except RuntimeError as e:
    submit("捕获:" + str(e))
submit("未捕获")
'''
    out = await run_program(code, rec, timeout=30)
    assert "捕获" in out and "炸了" in out


@pytest.mark.asyncio
async def test_output_budget_no_deadlock_frames_still_parsed():
    rec = _Recorder()
    code = '''
print("x" * 100000)          # 超预算输出，应被丢弃但不堵死
r = tools.run("shell_exec", {"command": "echo ok"})
submit("预算OK")
'''
    t0 = time.monotonic()
    out = await run_program(code, rec, timeout=30)
    elapsed = time.monotonic() - t0
    assert out == "预算OK"
    assert rec.calls == [("shell_exec", {"command": "echo ok"})]
    assert elapsed < 15


@pytest.mark.asyncio
async def test_wall_timeout_kills_process():
    rec = _Recorder()
    code = "import time\ntime.sleep(30)\n"
    t0 = time.monotonic()
    out = await run_program(code, rec, timeout=2)
    elapsed = time.monotonic() - t0
    assert "PTC 超时" in out and "2s" in out
    assert elapsed < 15


@pytest.mark.asyncio
async def test_unicode_roundtrip():
    rec = _Recorder()
    code = 'submit("中文 🎯 完成")\n'
    out = await run_program(code, rec, timeout=30)
    assert out == "中文 🎯 完成"


@pytest.mark.asyncio
async def test_nested_args_roundtrip():
    rec = _Recorder()
    code = '''
tools.run("edit_file", {"path": "b.py", "old_string": "x", "new_string": "y", "replace_all": True})
submit("ok")
'''
    out = await run_program(code, rec, timeout=30)
    assert out == "ok"
    assert rec.calls == [("edit_file", {"path": "b.py", "old_string": "x",
                                        "new_string": "y", "replace_all": True})]


@pytest.mark.asyncio
async def test_nonzero_exit_code_reported():
    rec = _Recorder()
    code = 'import sys\nprint("partial output")\nsys.exit(3)\n'
    out = await run_program(code, rec, timeout=30)
    assert "partial output" in out
    assert "退出码 3" in out
