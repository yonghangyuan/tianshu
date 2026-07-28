"""Cron 调度器 — 轻量定时任务引擎。

配置: ~/.tianshu/cron.yaml
格式:
  jobs:
    - name: daily_paper_scan
      cron: "0 9 * * *"
      task_type: paper_radar
      input: "搜索今日 RL+wargame 论文"
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import yaml


def _cron_file() -> Path:
    d = Path.home() / ".tianshu"
    d.mkdir(parents=True, exist_ok=True)
    return d / "cron.yaml"


def _parse_cron(expr: str) -> dict[str, set[int]]:
    """解析标准 5 字段 cron 表达式。"""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron: {expr}")
    names = ["minute", "hour", "day", "month", "weekday"]
    result: dict[str, set[int]] = {}
    for name, part in zip(names, parts):
        if part == "*":
            result[name] = set(range(0, 60 if name == "minute" else 24 if name == "hour" else 32 if name == "day" else 13 if name == "month" else 7))
        else:
            values = set()
            for chunk in part.split(","):
                if "-" in chunk:
                    lo, hi = chunk.split("-")
                    values.update(range(int(lo), int(hi) + 1))
                elif chunk.startswith("*/"):
                    step = int(chunk[2:])
                    upper = 60 if name == "minute" else 24 if name == "hour" else 32
                    values.update(range(0, upper, step))
                else:
                    values.add(int(chunk))
            result[name] = values
    return result


def _matches(cron: str, now: time.struct_time) -> bool:
    """检查当前时间是否匹配 cron。"""
    p = _parse_cron(cron)
    return (
        now.tm_min in p["minute"]
        and now.tm_hour in p["hour"]
        and now.tm_mday in p["day"]
        and now.tm_mon in p["month"]
        and now.tm_wday in p["weekday"]
    )


class CronScheduler:
    """轻量 cron 引擎。"""

    def __init__(self):
        self._jobs: list[dict[str, Any]] = []
        self._running = False
        self._last_run: dict[str, float] = {}  # name → last run timestamp

    def load(self) -> None:
        """从 cron.yaml 加载任务。"""
        f = _cron_file()
        if f.exists():
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            self._jobs = data.get("jobs", [])

    def save(self) -> None:
        f = _cron_file()
        f.write_text(yaml.dump({"jobs": self._jobs}, allow_unicode=True),
                     encoding="utf-8")

    def add(self, name: str, cron: str, task_type: str, input_text: str) -> None:
        self._jobs.append({
            "name": name, "cron": cron,
            "task_type": task_type, "input": input_text,
        })
        self.save()

    def remove(self, name: str) -> bool:
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j["name"] != name]
        self.save()
        return len(self._jobs) < before

    def list_jobs(self) -> list[dict]:
        return list(self._jobs)

    async def run_loop(self, core, interval: float = 15.0) -> None:
        """主循环——每 interval 秒检查一次。"""
        self._running = True
        while self._running:
            now = time.localtime()
            for job in self._jobs:
                name = job["name"]
                if _matches(job["cron"], now):
                    last = self._last_run.get(name, 0)
                    # 避免在同一分钟内重复触发
                    if time.time() - last > 60:
                        self._last_run[name] = time.time()
                        asyncio.create_task(self._execute(core, job))
            await asyncio.sleep(interval)

    async def _execute(self, core, job: dict) -> None:
        """执行一个 cron 任务。"""
        from ..sdk.models import AgentRequest
        try:
            await core.run(AgentRequest(
                input=job.get("input", ""),
                task_type=job.get("task_type", "conversation"),
            ))
        except Exception:
            pass

    def stop(self) -> None:
        self._running = False
