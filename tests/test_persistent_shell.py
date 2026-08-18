"""持久 shell（minimal 预设）测试 — 线程架构，跨 asyncio 事件循环。"""

import asyncio
import platform

import pytest

from tianshu.diyao.persistent_shell import PersistentShell

pytestmark = pytest.mark.skipif(
    platform.system() != "Windows", reason="持久 shell 面向 Windows cmd 设计"
)


@pytest.fixture
def shell():
    sh = PersistentShell()
    yield sh
    sh.stop()


def _run(sh, command, timeout=30):
    return asyncio.run(sh.run(command, timeout=timeout))


def test_echo(shell):
    out = _run(shell, "echo hello-tsh")
    assert "hello-tsh" in out


def test_cwd_persists_across_commands(shell, tmp_path):
    d = str(tmp_path).replace("\\", "/")
    _run(shell, f'cd /d "{d}"')
    out = _run(shell, "cd")
    assert out.lower().replace("\\", "/").strip().endswith(d.lower())


def test_env_persists_across_commands(shell):
    _run(shell, "set TSHTEST=42")
    out = _run(shell, "echo %TSHTEST%")
    assert "42" in out


def test_timeout_respawns_and_recovers(shell):
    # ping 持续约 10s，1s 超时 → 杀树 + 重生（cwd 丢失）
    out = _run(shell, "ping -n 10 127.0.0.1", timeout=1)
    assert "超时" in out
    # 重生后仍可用
    out2 = _run(shell, "echo after-reset")
    assert "after-reset" in out2


def test_gbk_output_decoded(shell):
    # chcp 65001 后子进程仍可能输出 GBK 字节 → 双回退解码
    code = "import sys;sys.stdout.buffer.write('你好'.encode('gbk') + b'\\r\\n')"
    out = _run(shell, f'python -c "{code}"')
    assert "你好" in out


def test_utf8_echo_chinese(shell):
    out = _run(shell, "echo 天枢测试")
    assert "天枢测试" in out


def test_cross_fresh_event_loop(shell):
    # 每次 asyncio.run 都是全新事件循环——线程架构跨 loop 可用
    out1 = asyncio.run(shell.run("echo a"))
    out2 = asyncio.run(shell.run("echo b"))
    assert "a" in out1
    assert "b" in out2


def test_stop_idempotent():
    sh = PersistentShell()
    sh.stop()
    sh.stop()  # 不抛异常
    out = asyncio.run(sh.run("echo x"))
    assert "已关闭" in out


def test_no_output_command(shell):
    out = _run(shell, "set TSHTEST=1")
    assert out == "(无输出)"
