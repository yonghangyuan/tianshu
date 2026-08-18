"""持久化 cmd.exe 会话 — cwd/env 跨调用保持（minimal 预设专用）。

线程驱动，跨 asyncio 事件循环可用：CLI 每轮对话独立 asyncio.run()，
asyncio subprocess 传输绑定事件循环、跨 turn 必炸。因此用
subprocess.Popen + 后台读线程 + queue.Queue，run() 经 asyncio.to_thread 桥接。

协议：spawn 后发送 `prompt __TSH__$_` 设置唯一 prompt 标记（已验证
piped cmd 会打印 prompt），每条命令的输出以 MARKER 行结束——读到
MARKER 即命令完成。超时 → 杀整个进程树 + 重生（cwd 丢失，输出注明）。
"""

from __future__ import annotations

import asyncio
import os
import queue
import subprocess
import threading
import time

MARKER = "__TSH__"           # prompt 标记（纯 ASCII，chcp 前后均可靠解码）
MAX_OUTPUT = 65536           # 输出累计上限（字符）


class PersistentShell:
    """Windows cmd.exe 持久会话；POSIX 上回退 /bin/sh 同样协议。"""

    def __init__(self, max_output: int = MAX_OUTPUT):
        self._proc: subprocess.Popen | None = None
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._reader: threading.Thread | None = None
        self._max_output = max_output
        self._stopped = False
        self._overflow = False
        self._spawn()

    # ── 生命周期 ─────────────────────────────────────────────────────

    def _spawn(self) -> None:
        import platform
        kwargs: dict = dict(
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if platform.system() == "Windows":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            args = ["cmd.exe", "/Q"]
        else:
            kwargs["start_new_session"] = True  # killpg 用
            args = ["/bin/sh", "-i"]  # 交互模式才会打 prompt
        self._proc = subprocess.Popen(args, **kwargs)
        self._stopped = False
        # 后台读线程：持续消费 stdout → queue，防管道堵死
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()
        # 设置 prompt 标记，然后同步到首个 MARKER。
        # 注意：不 chcp 65001——cmd 读管道 stdin 仍按原代码页(GBK)解析，
        # UTF-8 中文命令会被误解析挂起；stdin 用 GBK 编码、输出双回退解码。
        self._send("prompt __TSH__$_")
        try:
            self._sync(timeout=10)
        except TimeoutError:
            # 启动失败也继续——run 时再处理
            pass
        # 启动输出经管道有滞后：MARKER/空行可能迟到，
        # 静置排空，确保下一条命令前队列干净
        self._settle()

    def stop(self) -> None:
        """终止会话（同步、跨事件循环安全、幂等）。"""
        self._stopped = True
        if self._proc and self._proc.poll() is None:
            self._kill_tree()
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=3)
            except Exception:
                pass
        self._proc = None

    def _kill_tree(self) -> None:
        """杀掉 cmd 及全部子进程（孙进程）。"""
        import platform
        if self._proc is None:
            return
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                    capture_output=True, timeout=10,
                )
            else:
                os.killpg(self._proc.pid, 9)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass

    # ── 内部 ─────────────────────────────────────────────────────────

    def _reader_loop(self) -> None:
        """读线程：逐行读 stdout 放入队列。MARKER 永不丢；普通行超
        512 行上限后丢弃但持续消费（防管道堵死），并置溢出标记。"""
        while self._proc is not None and self._proc.stdout is not None:
            try:
                data = self._proc.stdout.readline()
            except Exception:
                break
            if not data:
                break  # EOF
            line = self._decode_line(data)
            if line.strip() == MARKER:
                self._queue.put(line)          # 命令完成信号不能丢
            elif self._queue.qsize() < 512:
                self._queue.put(line)
            else:
                self._overflow = True          # 丢弃但持续消费

    @staticmethod
    def _decode_line(data: bytes) -> str:
        """utf-8 优先，出现替换符再试 GBK（镜像 sandbox 的 Windows 策略）。"""
        s = data.decode("utf-8", errors="replace")
        if "�" in s:
            try:
                return data.decode("gbk", errors="replace")
            except Exception:
                pass
        return s

    def _send(self, text: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            # GBK 编码：cmd 管道 stdin 按原代码页解析（见 _spawn 注释）
            self._proc.stdin.write((text + "\r\n").encode("gbk", errors="replace"))
            self._proc.stdin.flush()
        except Exception:
            pass

    def _sync(self, timeout: float = 10) -> None:
        """阻塞读队列直到首个 MARKER 行（丢弃启动噪声）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if line.strip() == MARKER:
                return
        raise TimeoutError("shell sync timeout")

    def _settle(self, quiet: float = 0.4, deadline_s: float = 3.0) -> None:
        """等待管道安静并清空队列——cmd 启动输出经管道有滞后，
        _sync 只消费第一个 MARKER，剩余的残留行在安静窗口后清掉。"""
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            try:
                self._queue.get(timeout=quiet)
                # 有新行 → 回到循环头，继续等待下一个安静窗口
            except queue.Empty:
                return  # 安静窗口内无新行 → 管道已排空
        # 兜底：硬超时后直接清空
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _run_sync(self, command: str, timeout: float) -> str:
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()
        # 防御性清理：命令严格串行，发送前队列里的一切都是陈旧残留
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        self._overflow = False
        self._send(command)

        lines: list[str] = []
        total = 0
        truncated = False
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # 超时：无法只杀当前命令 → 杀树 + 重生（cwd 丢失）
                self._kill_tree()
                self._spawn()
                return f"[shell 超时 {timeout:.0f}s，会话已重置，cwd 丢失]"
            try:
                line = self._queue.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if line.strip() == MARKER:
                break
            if total + len(line) <= self._max_output:
                lines.append(line)
                total += len(line)
            else:
                truncated = True
                # 剩余行丢弃但持续消费到 MARKER（防队列膨胀）

        # 去回显：/Q 默认关回显，防御性剥掉首行==命令本身
        if lines and lines[0].rstrip("\r\n") == command.rstrip():
            lines = lines[1:]

        result = "".join(lines).strip()
        if truncated or self._overflow:
            result += "\n[输出已截断]"
        return result or "(无输出)"

    # ── 对外 API ─────────────────────────────────────────────────────

    async def run(self, command: str, timeout: float = 60.0) -> str:
        """执行一条命令，返回输出。cwd/env 在会话内保持。"""
        if self._stopped:
            return "[shell 已关闭]"
        return await asyncio.to_thread(self._run_sync, command, timeout)
