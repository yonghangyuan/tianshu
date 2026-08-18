"""PTC — Program Tool Composition：模型写 Python 程序组合工具调用，跑在子进程。

思想（借鉴 DSH Code Mode）：模型写代码比发工具调用更擅长。程序内通过
同步 API `tools.run(name, args)` 调用工具，中间结果留在子进程、不回模型
上下文——只有 `submit(value)`（或 stdout 尾部）作为 run_code 的唯一返回。

stdio 帧协议（行分隔，控制字符前缀防误判）：
  程序→父  \\x1eTIANSHU_TOOL\\x1f{json}     工具请求 {"name","args"}
  父→程序  \\x1eTIANSHU_RESULT\\x1f{json}   结果 {"result": str} | {"error": str}
  程序→父  \\x1eTIANSHU_SUBMIT\\x1f{json}   最终结果（程序随即退出）

防死锁不变量：父进程始终排空 stdout（输出预算耗尽后仍解析帧）；
RESULT 帧 ≤8KB < 64KB 管道缓冲；子进程仅在发出 TOOL 帧后阻塞读 stdin，
父此刻写入必不阻塞。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Awaitable, Callable

FRAME_TOOL = "\x1eTIANSHU_TOOL\x1f"
FRAME_RESULT = "\x1eTIANSHU_RESULT\x1f"
FRAME_SUBMIT = "\x1eTIANSHU_SUBMIT\x1f"

MAX_TOOL_RESULT = 8192    # 单次工具结果上限（字符）
MAX_SUBMIT = 8192         # submit 值上限
DEFAULT_TIMEOUT = 300.0   # 墙钟超时（秒）
DEFAULT_MAX_OUTPUT = 65536  # 输出累计上限（字符）

BOOTSTRAP = '''\
# 由天枢注入 — 程序内可用: tools.run(name, args) / submit(value)
import sys as _sys, json as _json

_TOOL = "\\x1eTIANSHU_TOOL\\x1f"
_RESULT = "\\x1eTIANSHU_RESULT\\x1f"
_SUBMIT = "\\x1eTIANSHU_SUBMIT\\x1f"

class _RpcError(RuntimeError):
    pass

def _send(prefix, payload):
    _sys.stdout.write(prefix + _json.dumps(payload, ensure_ascii=False, default=str) + "\\n")
    _sys.stdout.flush()

class _Tools:
    def run(self, name, args=None):
        """同步调用工具，返回字符串结果。被策略拒绝时抛 RuntimeError。"""
        _send(_TOOL, {"name": name, "args": args or {}})
        line = _sys.stdin.readline()
        if not line:
            raise _RpcError("父进程已关闭")
        payload = _json.loads(line[len(_RESULT):])
        if payload.get("error"):
            raise _RpcError(payload["error"])
        return payload.get("result", "")

tools = _Tools()

def submit(value):
    """提交最终结果并立即结束程序——该值作为 run_code 的唯一返回。"""
    _send(_SUBMIT, {"value": value})
    _sys.exit(0)
'''

ExecTool = Callable[[str, dict], Awaitable[str]]


def _safe_work_dir(cwd: str) -> str:
    """返回一个确定存在的工作目录（镜像 sandbox._safe_work_dir）。"""
    if cwd and os.path.isdir(cwd):
        return cwd
    try:
        if os.path.isdir(os.getcwd()):
            return os.getcwd()
    except Exception:
        pass
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or tempfile.gettempdir()
    return home if os.path.isdir(home) else tempfile.gettempdir()


def _kill_proc_tree(proc: subprocess.Popen) -> None:
    """杀进程树（Windows: taskkill /T /F；POSIX: killpg）。"""
    import platform
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        else:
            os.killpg(proc.pid, 9)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def run_program(
    code: str,
    exec_tool: ExecTool,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
    cwd: str = "",
) -> str:
    """在子进程执行程序，桥接工具调用，返回最终结果。"""
    # 1. 临时文件 = BOOTSTRAP + 用户代码
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    tmp.write(BOOTSTRAP)
    tmp.write("\n\n# ── 用户程序 ────────────────────────────────\n")
    tmp.write(code)
    tmp_path = tmp.name
    tmp.close()

    import platform
    kwargs: dict[str, Any] = dict(
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # 单管道合并，无双管道死锁
        cwd=_safe_work_dir(cwd),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if platform.system() != "Windows":
        kwargs["start_new_session"] = True  # killpg 用
    if platform.system() == "Windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = await asyncio.create_subprocess_exec(sys.executable, tmp_path, **kwargs)

    submit_value: str | None = None
    stdout_buf: list[str] = []
    total = 0

    try:
        async def _handle_tool_frame(line: str) -> None:
            try:
                payload = json.loads(line[len(FRAME_TOOL):])
                name = str(payload.get("name", ""))
                args = payload.get("args") or {}
                if not isinstance(args, dict):
                    args = {}
            except Exception as e:
                result = {"error": f"工具请求帧解析失败: {e}"}
            else:
                try:
                    result = {"result": str(await exec_tool(name, args))[:MAX_TOOL_RESULT]}
                except Exception as e:
                    result = {"error": f"{type(e).__name__}: {e}"}
            # 写 RESULT 帧——子进程此刻正阻塞读 stdin，管道必有余量
            try:
                proc.stdin.write(
                    (FRAME_RESULT + json.dumps(result, ensure_ascii=False) + "\n")
                    .encode("utf-8")
                )
                await proc.stdin.drain()
            except Exception:
                # 子进程中途死亡 → 视为程序终止
                raise _ProgramDied()

        async def _pump() -> None:
            nonlocal submit_value, total
            # 手动分块读取 + 自建行缓冲：绕开 StreamReader.readline 的
            # 64KB 行长限制（超长单行由截断逻辑兜底）。帧解析在输出
            # 预算耗尽后继续（防死锁关键）。
            buf = b""
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break  # EOF
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace")
                    if line.startswith(FRAME_TOOL):
                        await _handle_tool_frame(line)
                    elif line.startswith(FRAME_SUBMIT):
                        try:
                            payload = json.loads(line[len(FRAME_SUBMIT):])
                            submit_value = str(payload.get("value", ""))
                        except Exception:
                            submit_value = line[len(FRAME_SUBMIT):].strip()
                        buf = b""
                        return
                    else:
                        if total + len(line) + 1 <= max_output:
                            stdout_buf.append(line + "\n")
                            total += len(line) + 1
                        # 超预算丢弃但继续消费
                # 无换行的超长行：截断前缀防内存膨胀（帧都是完整行，不受影响）
                if len(buf) > 1 << 20:
                    buf = buf[-65536:]
            # EOF 尾部无换行的残留
            if buf:
                line = buf.decode("utf-8", errors="replace")
                if line.startswith(FRAME_TOOL):
                    await _handle_tool_frame(line)
                elif line.startswith(FRAME_SUBMIT):
                    try:
                        payload = json.loads(line[len(FRAME_SUBMIT):])
                        submit_value = str(payload.get("value", ""))
                    except Exception:
                        submit_value = line[len(FRAME_SUBMIT):].strip()
                elif total + len(line) + 1 <= max_output:
                    stdout_buf.append(line + "\n")

        try:
            await asyncio.wait_for(_pump(), timeout=timeout)
        except _ProgramDied:
            pass
        except asyncio.TimeoutError:
            await asyncio.to_thread(_kill_proc_tree, proc)
            await _reap(proc)
            return f"[PTC 超时 {timeout:.0f}s，已终止]"

        if submit_value is not None:
            return submit_value[:MAX_SUBMIT]

        await _reap(proc)
        exit_code = proc.returncode or 0
        tail = "".join(stdout_buf)[-2000:]
        if not tail.strip():
            return f"(程序无输出，退出码 {exit_code})"
        if exit_code != 0:
            return f"{tail}\n[程序退出码 {exit_code}]"
        return tail
    finally:
        # 收尸 + 清理临时文件（进程已死，Windows 可删）
        if proc.returncode is None:
            await asyncio.to_thread(_kill_proc_tree, proc)
        await _reap(proc)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


class _ProgramDied(Exception):
    """子程序在等待工具结果时死亡。"""


async def _reap(proc: subprocess.Popen) -> None:
    """收尸——防止 transport 泄漏（GC 时报 Event loop is closed）。"""
    try:
        await proc.wait()
    except Exception:
        pass
