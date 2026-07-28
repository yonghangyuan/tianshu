"""Docker 沙箱执行——隔离代码运行、文件操作。

用法:
    sandbox = DockerSandbox()
    result = await sandbox.run("python -c 'print(1+1)'")
    # or local fallback:
    sandbox = LocalSandbox()
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any


class SandboxBase:
    """沙箱抽象——所有执行环境实现此接口。"""

    async def run(self, command: str, cwd: str = "", timeout: int = 30) -> SandboxResult:
        raise NotImplementedError

    async def run_python(self, code: str, timeout: int = 30) -> SandboxResult:
        raise NotImplementedError


class SandboxResult:
    def __init__(self, stdout: str, stderr: str, exit_code: int, elapsed: float):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.elapsed = elapsed
        self.ok = exit_code == 0

    def __str__(self) -> str:
        return self.stdout or self.stderr or f"exit={self.exit_code}"


class LocalSandbox(SandboxBase):
    """本地执行（开发用）——不隔离，但快。跨平台兼容。"""

    @staticmethod
    def _safe_work_dir(cwd: str) -> str:
        """返回一个确定存在的工作目录。"""
        import platform
        # 尝试 cwd
        if cwd and os.path.isdir(cwd):
            return cwd
        # 尝试当前目录
        try:
            if os.path.isdir(os.getcwd()):
                return os.getcwd()
        except Exception:
            pass
        # 回退到 HOME 或 USERPROFILE
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or str(Path.home())
        if os.path.isdir(home):
            return home
        # 最后手段：temp 目录
        return tempfile.gettempdir()

    @staticmethod
    def _find_python() -> str:
        """找到可用的 Python 解释器。"""
        import platform
        if platform.system() != "Windows":
            for name in ("python3", "python"):
                if shutil_which(name):
                    return name
            return "python3"
        # Windows: 尝试多种方式
        for name in ("python", "python3", "py"):
            if shutil_which(name):
                return name
        # 最后回退：用 sys.executable 的完整路径
        import sys
        return sys.executable

    async def run(self, command: str, cwd: str = "", timeout: int = 30) -> SandboxResult:
        import platform
        import time
        t0 = time.time()

        work_dir = self._safe_work_dir(cwd)

        # Windows: 用 cmd /c 包装；Unix: 用 sh -c
        if platform.system() == "Windows":
            # cmd /c 不需要外层引号——直接用即可
            shell_cmd = f"cmd /c {command}"
            encoding = "gbk"
        else:
            shell_cmd = command
            encoding = "utf-8"

        try:
            proc = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
            return SandboxResult(
                stdout=stdout.decode(encoding, errors="replace")[:5000],
                stderr=stderr.decode(encoding, errors="replace")[:2000],
                exit_code=proc.returncode or 0,
                elapsed=time.time() - t0,
            )
        except asyncio.TimeoutError:
            return SandboxResult("", f"Timeout after {timeout}s", -1, time.time() - t0)
        except Exception as e:
            return SandboxResult("", f"{type(e).__name__}: {e}", -1, time.time() - t0)

    async def run_python(self, code: str, timeout: int = 30) -> SandboxResult:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name
        try:
            py = self._find_python()
            return await self.run(f'{py} "{tmp_path}"', timeout=timeout)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def shutil_which(name: str) -> str | None:
    """shutil.which 的轻量替代——避免 import shutil 开销。"""
    import os as _os
    path_ext = _os.environ.get("PATHEXT", "").split(_os.pathsep) if _os.name == "nt" else []
    for p in _os.environ.get("PATH", "").split(_os.pathsep):
        full = _os.path.join(p, name)
        if _os.path.isfile(full):
            return full
        for ext in path_ext:
            full_ext = full + ext
            if _os.path.isfile(full_ext):
                return full_ext
    return None


class DockerSandbox(SandboxBase):
    """Docker 容器执行——隔离，安全。

    Requires: docker (docker.io or docker desktop)
    Image: python:3.11-slim (auto-pulled on first use)
    """

    IMAGE = "python:3.11-slim"

    def __init__(self, image: str = ""):
        self._image = image or self.IMAGE
        self._available: bool | None = None

    async def _check_docker(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            self._available = proc.returncode == 0
        except FileNotFoundError:
            self._available = False
        return self._available

    async def run(
        self, command: str, cwd: str = "", timeout: int = 30
    ) -> SandboxResult:
        if not await self._check_docker():
            # Fallback to local — 不传 Linux 路径给 Windows
            local = LocalSandbox()
            return await local.run(command, cwd="", timeout=timeout)

        # cwd 用于 Docker volume mount，需要绝对路径
        docker_cwd = cwd or os.getcwd()

        import time
        t0 = time.time()
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "docker", "run", "--rm",
                    "--network", "none",       # no network
                    "--memory", "256m",         # limit memory
                    "--cpus", "1",              # limit CPU
                    "--read-only",              # read-only rootfs
                    "--tmpfs", "/tmp:exec",     # writable /tmp
                    "-v", f"{docker_cwd}:/workspace:ro",
                    self._image,
                    "sh", "-c", command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=timeout,
            )
            stdout, stderr = await proc.communicate()
            return SandboxResult(
                stdout=stdout.decode("utf-8", errors="replace")[:5000],
                stderr=stderr.decode("utf-8", errors="replace")[:2000],
                exit_code=proc.returncode or 0,
                elapsed=time.time() - t0,
            )
        except asyncio.TimeoutError:
            return SandboxResult("", f"Timeout {timeout}s", -1, time.time() - t0)

    async def run_python(self, code: str, timeout: int = 30) -> SandboxResult:
        return await self.run(f"python -c '{code}'", timeout=timeout)
