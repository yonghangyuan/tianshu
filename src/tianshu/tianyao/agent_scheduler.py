"""Agent 时间尺度调度器 — 让 Agent 按声明的 tick 真实运行。

与 CronScheduler 不同: CronScheduler 是人类配置的定时任务;
AgentScheduler 是 Agent 自身的生命节律——每个 Agent 按自己的
TimeScale.tick_ms 周期性地感知、报告、或仲裁。

用法:
    scheduler = AgentScheduler()
    scheduler.register(agent_ref, time_scale, callback)
    await scheduler.start()
    # ... agents tick autonomously ...
    await scheduler.stop()
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from ..sdk.trigram import AgentRef, TimeScale, TrigramMessage, SyncMode


@dataclass
class AgentTick:
    """一次 Agent tick 的记录。"""
    agent: AgentRef
    tick_index: int
    elapsed_ms: int
    message: TrigramMessage | None = None
    skipped: bool = False  # THRESHOLD 模式下变化不足被跳过


TickCallback = Callable[[AgentRef, int, float], Awaitable[TrigramMessage | None]]
"""回调签名: (agent_ref, tick_index, elapsed_since_start_s) → TrigramMessage | None
返回 None 表示本轮无报告（THRESHOLD/PULL 模式常见）。
"""


class AgentScheduler:
    """Agent 时间尺度调度器。

    每个注册的 Agent 按自己的 tick_ms 异步运行。
    支持三种 SyncMode:
      PUSH:      每次 tick 都调用回调，回调返回的消息立即入队
      PULL:      只在外部 poll() 时调用
      THRESHOLD: 每次 tick 都调用，但回调返回 None 则跳过（变化不足）
    """

    def __init__(self):
        self._agents: dict[str, dict[str, Any]] = {}  # agent_id → {ref, scale, callback, task}
        self._running = False
        self._tick_log: list[AgentTick] = []  # 最近 N 条 tick 记录
        self._queue: asyncio.Queue[TrigramMessage] = asyncio.Queue()

    # ── 注册 ───────────────────────────────────────────────────────

    def register(
        self,
        agent: AgentRef,
        time_scale: TimeScale,
        callback: TickCallback,
    ) -> None:
        """注册一个 Agent 进入调度循环。

        Args:
            agent: Agent 标识
            time_scale: 时间尺度（tick_ms, sync mode, decay）
            callback: 每次 tick 调用的函数
        """
        agent_id = str(agent)
        self._agents[agent_id] = {
            "ref": agent,
            "scale": time_scale,
            "callback": callback,
            "task": None,
            "tick_count": 0,
            "last_value": None,  # THRESHOLD 模式记录上次值
        }

    def unregister(self, agent: AgentRef) -> bool:
        """取消注册。"""
        agent_id = str(agent)
        if agent_id in self._agents:
            info = self._agents.pop(agent_id)
            if info["task"]:
                info["task"].cancel()
            return True
        return False

    # ── 生命周期 ───────────────────────────────────────────────────

    async def start(self) -> None:
        """启动所有已注册 Agent 的调度循环。"""
        self._running = True
        for agent_id, info in self._agents.items():
            if info["task"] is None:
                info["task"] = asyncio.create_task(
                    self._agent_loop(agent_id)
                )

    async def stop(self) -> None:
        """停止所有 Agent。"""
        self._running = False
        for info in self._agents.values():
            if info["task"]:
                info["task"].cancel()
                info["task"] = None

    # ── 消息队列 ──────────────────────────────────────────────────

    async def poll(self, agent: AgentRef | None = None) -> TrigramMessage | None:
        """PULL 模式: 手动触发一次 Agent tick。agent=None 时等待队列中的下一条消息。"""
        if agent is not None:
            info = self._agents.get(str(agent))
            if info and info["scale"].sync == SyncMode.PULL:
                t0 = time.time()
                msg = await info["callback"](
                    agent, info["tick_count"],
                    time.time() - self._start_time if hasattr(self, '_start_time') else 0,
                )
                info["tick_count"] += 1
                if msg:
                    self._tick_log.append(AgentTick(
                        agent=agent, tick_index=info["tick_count"],
                        elapsed_ms=int((time.time() - t0) * 1000), message=msg,
                    ))
                return msg
            return None

        # 否则从队列取
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return None

    # ── 统计 ───────────────────────────────────────────────────────

    @property
    def tick_log(self) -> list[AgentTick]:
        return list(self._tick_log[-100:])  # 最近 100 条

    @property
    def active_agents(self) -> int:
        return len(self._agents)

    def agent_stats(self) -> dict[str, dict]:
        """各 Agent 的统计。"""
        return {
            aid: {"tick_count": info["tick_count"], "tick_ms": info["scale"].tick_ms}
            for aid, info in self._agents.items()
        }

    # ── 内部 ───────────────────────────────────────────────────────

    async def _agent_loop(self, agent_id: str) -> None:
        """单个 Agent 的调度循环。"""
        info = self._agents[agent_id]
        ref = info["ref"]
        scale: TimeScale = info["scale"]
        callback: TickCallback = info["callback"]
        tick_ms = scale.tick_ms / 1000.0  # 转为秒

        self._start_time = getattr(self, '_start_time', time.time())
        t_start = time.time()

        while self._running:
            t0 = time.time()

            # PULL 模式: 不自动 tick，等 poll()
            if scale.sync == SyncMode.PULL:
                await asyncio.sleep(1.0)
                continue

            # PUSH / THRESHOLD 模式: 自动 tick
            try:
                msg = await callback(ref, info["tick_count"], time.time() - t_start)
            except Exception:
                msg = None

            info["tick_count"] += 1
            elapsed = int((time.time() - t0) * 1000)

            # THRESHOLD 模式: 检查变化是否超阈值
            skipped = False
            if scale.sync == SyncMode.THRESHOLD and msg is None:
                skipped = True

            tick_record = AgentTick(
                agent=ref,
                tick_index=info["tick_count"],
                elapsed_ms=elapsed,
                message=msg,
                skipped=skipped,
            )
            self._tick_log.append(tick_record)

            # PUSH 或有效 THRESHOLD → 入队
            if msg is not None:
                await self._queue.put(msg)

            # 等待下一个 tick（补偿执行时间）
            elapsed_sec = time.time() - t0
            sleep_time = max(0, tick_ms - elapsed_sec)
            await asyncio.sleep(sleep_time)
