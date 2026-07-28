"""Sandbox 测试 — Windows + Unix 兼容性。"""

import sys
import platform
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root / "src"))

from tianshu.diyao.sandbox import LocalSandbox, DockerSandbox, SandboxResult


class TestLocalSandbox:
    """LocalSandbox 跨平台测试。"""

    @pytest.mark.asyncio
    async def test_echo(self):
        s = LocalSandbox()
        r = await s.run("echo hello", timeout=5)
        assert r.ok
        assert "hello" in r.stdout

    @pytest.mark.asyncio
    async def test_python_execution(self):
        s = LocalSandbox()
        py = s._find_python()
        assert py, "No Python found"
        r = await s.run(f'{py} -c "print(42)"', timeout=10)
        assert r.ok, f"Python failed: {r.stderr}"
        assert "42" in r.stdout

    @pytest.mark.asyncio
    async def test_bad_cwd_fallback(self):
        """坏的工作目录应回退，不抛 WinError 267。"""
        s = LocalSandbox()
        r = await s.run("echo ok", cwd="/nonexistent/linux/path", timeout=5)
        assert r.ok, f"Should not crash: {r}"
        assert "ok" in r.stdout

    @pytest.mark.asyncio
    async def test_timeout(self):
        s = LocalSandbox()
        py = s._find_python()
        # Python 脚本 sleep 5 秒，timeout=1 秒应触发超时
        r = await s.run(f'{py} -c "import time; time.sleep(5)"', timeout=1)
        assert not r.ok
        assert "Timeout" in r.stderr

    @pytest.mark.asyncio
    async def test_run_python(self):
        s = LocalSandbox()
        r = await s.run_python("print('hello from python')", timeout=10)
        assert r.ok, f"run_python failed: {r}"
        assert "hello from python" in r.stdout

    @pytest.mark.asyncio
    async def test_find_python_returns_string(self):
        s = LocalSandbox()
        py = s._find_python()
        assert isinstance(py, str)
        assert len(py) > 0

    @pytest.mark.asyncio
    async def test_safe_work_dir_always_valid(self):
        s = LocalSandbox()
        wd = s._safe_work_dir("")
        assert Path(wd).is_dir(), f"Work dir not valid: {wd}"

        wd2 = s._safe_work_dir("/bogus/path/that/does/not/exist")
        assert Path(wd2).is_dir()

    @pytest.mark.asyncio
    async def test_command_with_special_chars(self):
        """包含特殊字符的命令不应崩溃。"""
        s = LocalSandbox()
        r = await s.run('echo "hello & world"', timeout=5)
        # 不检查 ok（Windows cmd 可能返回非零），只确保不抛异常
        assert isinstance(r, SandboxResult)


class TestDockerSandbox:
    """DockerSandbox 测试（Docker 不可用时 fallback 到 Local）。"""

    @pytest.mark.asyncio
    async def test_fallback_to_local(self):
        """没有 Docker 时应自动降级到 LocalSandbox。"""
        s = DockerSandbox()
        r = await s.run("echo fallback", timeout=5)
        assert r.ok
        assert "fallback" in r.stdout

    @pytest.mark.asyncio
    async def test_no_crash_bad_cwd(self):
        """Docker fallback + 坏 cwd 不应崩溃。"""
        s = DockerSandbox()
        r = await s.run("echo ok", cwd="/workspace", timeout=5)
        assert r.ok, f"Should not crash: {r}"
