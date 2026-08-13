"""星群消息总线 — Agent 间直接通信（ROADMAP P2-006）。

对标 OpenClaw HiClaw (Matrix 协议)。子 Agent 不再只经调度器中转，
通过总线直接互发消息、共享记忆板、参与话题广播。

三种通道:
  direct    点对点消息 (mailbox, TTL 过期自动过滤)
  broadcast 话题广播 (topic → 订阅者 inbox)
  board     共享记忆板 (key-value, 版本历史可审计)

调用方身份: 工具执行时通过 contextvars 自动归属（并发安全），
调度器在 dispatch 前设置，Agent 调 send_message 等工具时无需自报身份。
"""

from __future__ import annotations

import contextvars
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# 当前正在执行的 Agent 名字（contextvars 并发安全）
_current_agent: contextvars.ContextVar[str] = contextvars.ContextVar(
    "starbus_current_agent", default="unknown"
)


def set_current_agent(name: str) -> contextvars.Token:
    """设置当前 Agent 身份（调度器 dispatch 时调用），返回还原 token。"""
    return _current_agent.set(name)


def reset_current_agent(token: contextvars.Token) -> None:
    _current_agent.reset(token)


def current_agent() -> str:
    return _current_agent.get()


# ── 数据类型 ───────────────────────────────────────────────────────────────

@dataclass
class BusMessage:
    """总线上的一条消息。"""
    msg_id: str
    source: str                    # 发送方 Agent 名
    target: str                    # 接收方 Agent 名（"" = 广播）
    topic: str = ""                # 广播话题
    intent: str = ""               # 消息摘要
    payload: dict[str, Any] = field(default_factory=dict)
    ttl_ms: int = 60_000           # 生存期，过期后自动过滤
    created: float = 0.0

    def is_expired(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return (now - self.created) * 1000 > self.ttl_ms

    def age_ms(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        return int((now - self.created) * 1000)


@dataclass
class BoardEntry:
    """共享记忆板条目——版本历史可审计。"""
    key: str
    value: str
    source: str                    # 最后写入者
    created: float
    updated: float
    version: int = 1
    history: list[dict] = field(default_factory=list)   # [{version, value, source, at}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "value": self.value, "source": self.source,
            "version": self.version, "created": self.created,
            "updated": self.updated, "history": self.history,
        }


# ── 总线 ───────────────────────────────────────────────────────────────────

class StarBus:
    """星群消息总线。

    用法:
        bus = StarBus()
        bus.send("searcher", "analyst", "找到 3 篇文献", {"refs": [...]})
        bus.publish("osint", "searcher", "突发事件")
        bus.post("结论", "方案A更优", "analyst")
        msgs = bus.pop_all("analyst")
    """

    def __init__(self, max_inbox: int = 100) -> None:
        self._inboxes: dict[str, list[BusMessage]] = defaultdict(list)
        self._topics: dict[str, set[str]] = defaultdict(set)   # topic → 订阅者
        self._board: dict[str, BoardEntry] = {}
        self._log: list[dict] = []        # 全量消息日志（审计）
        self._max_inbox = max_inbox

    # ── direct: 点对点 ─────────────────────────────────────────────

    def send(self, source: str, target: str, intent: str,
             payload: dict[str, Any] | None = None,
             ttl_ms: int = 60_000) -> BusMessage:
        """点对点发送消息到目标 Agent 的收件箱。"""
        if not target:
            raise ValueError("点对点消息必须指定 target")
        msg = BusMessage(
            msg_id=f"m_{uuid.uuid4().hex[:8]}",
            source=source, target=target, intent=intent,
            payload=payload or {}, ttl_ms=ttl_ms, created=time.time(),
        )
        self._deliver(msg)
        return msg

    def inbox(self, agent: str, include_expired: bool = False) -> list[BusMessage]:
        """查看收件箱（默认过滤已过期消息，不清空）。"""
        msgs = list(self._inboxes.get(agent, []))
        if not include_expired:
            msgs = [m for m in msgs if not m.is_expired()]
        return msgs

    def pop_all(self, agent: str) -> list[BusMessage]:
        """取出并清空收件箱（过滤过期）。"""
        live = self.inbox(agent)
        expired = [m for m in self._inboxes.get(agent, []) if m.is_expired()]
        self._inboxes[agent] = expired   # 过期消息保留到下次清理
        return live

    def unread_count(self, agent: str) -> int:
        return len(self.inbox(agent))

    # ── broadcast: 话题 ────────────────────────────────────────────

    def subscribe(self, topic: str, agent: str) -> None:
        self._topics[topic].add(agent)

    def unsubscribe(self, topic: str, agent: str) -> None:
        self._topics.get(topic, set()).discard(agent)

    def publish(self, topic: str, source: str, intent: str,
                payload: dict[str, Any] | None = None,
                ttl_ms: int = 60_000) -> list[BusMessage]:
        """发布到话题 → 投递到所有订阅者的收件箱。"""
        sent: list[BusMessage] = []
        for sub in sorted(self._topics.get(topic, set())):
            msg = BusMessage(
                msg_id=f"m_{uuid.uuid4().hex[:8]}",
                source=source, target=sub, topic=topic, intent=intent,
                payload=payload or {}, ttl_ms=ttl_ms, created=time.time(),
            )
            self._deliver(msg)
            sent.append(msg)
        return sent

    def subscribers(self, topic: str) -> list[str]:
        return sorted(self._topics.get(topic, set()))

    def topics(self) -> dict[str, list[str]]:
        return {t: self.subscribers(t) for t in sorted(self._topics)}

    # ── board: 共享记忆板 ──────────────────────────────────────────

    def post(self, key: str, value: str, source: str) -> BoardEntry:
        """写入共享记忆板——同 key 覆盖并版本 +1，历史保留。"""
        now = time.time()
        entry = self._board.get(key)
        if entry is None:
            entry = BoardEntry(key=key, value=value, source=source,
                               created=now, updated=now)
        else:
            entry.history.append({
                "version": entry.version, "value": entry.value,
                "source": entry.source, "at": entry.updated,
            })
            entry.value = value
            entry.source = source
            entry.updated = now
            entry.version += 1
        self._board[key] = entry
        return entry

    def read(self, key: str) -> BoardEntry | None:
        return self._board.get(key)

    def keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._board if k.startswith(prefix))

    def board_snapshot(self, prefix: str = "", limit: int = 20) -> list[dict]:
        """记忆板快照（注入子 Agent 上下文用）。"""
        return [
            self._board[k].to_dict()
            for k in self.keys(prefix)[:limit]
        ]

    # ── 生命周期 / 审计 ────────────────────────────────────────────

    def _deliver(self, msg: BusMessage) -> None:
        box = self._inboxes[msg.target]
        box.append(msg)
        if len(box) > self._max_inbox:
            del box[:len(box) - self._max_inbox]
        self._log.append({
            "msg_id": msg.msg_id, "source": msg.source, "target": msg.target,
            "topic": msg.topic, "intent": msg.intent[:120], "created": msg.created,
        })

    def clear_agent(self, agent: str) -> None:
        """销毁 Agent 时清理其收件箱与订阅。"""
        self._inboxes.pop(agent, None)
        for subs in self._topics.values():
            subs.discard(agent)

    def stats(self) -> dict[str, Any]:
        return {
            "inboxes": {a: len(self.inbox(a)) for a in self._inboxes},
            "topics": self.topics(),
            "board_keys": len(self._board),
            "total_messages": len(self._log),
        }
