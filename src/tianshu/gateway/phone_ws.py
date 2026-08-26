"""手机控制 WS 通道 — 手机桥（PhoneBridge.kt）的 PC 侧端点。

架构（docs/ANDROID_CONTROL_PLAN.md §二）：手机是哑终端，
PC 天枢经 /ws/phone 下发 JSON-RPC（screen_state/tap/...），
手机回 {"id": n, "result": {...}}。手机本质上是一个
"物理世界 MCP Server"。

本模块只管连接与转发；工具注册在 renyao/skills/phone.py，
策略闸门走 AgentCore 三爻既有链路。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import WebSocket

# ── 连接注册表 ────────────────────────────────────────────────

_phone_ws: WebSocket | None = None
_phone_meta: dict[str, Any] = {}
_phone_lock = asyncio.Lock()
# 挂起请求: rpc_id → Future（工具调用 await 手机响应）
_pending: dict[int, asyncio.Future] = {}
_rpc_seq = 0
_last_heartbeat: float = 0.0


def phone_connected() -> bool:
    """手机桥是否在线（CLI /status 展示用）。"""
    return _phone_ws is not None


def phone_status() -> dict[str, Any]:
    """手机桥状态摘要。"""
    return {
        "connected": _phone_ws is not None,
        "device": _phone_meta.get("device", ""),
        "last_heartbeat": _last_heartbeat,
    }


# ── RPC 调用（工具侧入口）────────────────────────────────────

async def phone_rpc(method: str, params: dict | None = None, timeout: float = 15.0) -> dict:
    """向手机下发 RPC 并等待响应。

    Returns:
        手机回的 result/error dict；超时/离线返回 {"error": ...}。
        绝不抛异常——工具层要的是可读错误，不是堆栈。
    """
    global _rpc_seq
    if _phone_ws is None:
        return {"error": "手机未连接（检查 PhoneBridge 与 adb reverse）"}

    async with _phone_lock:
        _rpc_seq += 1
        rpc_id = _rpc_seq
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending[rpc_id] = fut

    payload = json.dumps({"id": rpc_id, "method": method, "params": params or {}},
                         ensure_ascii=False)
    try:
        await _phone_ws.send_text(payload)
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": f"手机响应超时 ({method}, {timeout}s)"}
    except Exception as e:
        return {"error": f"手机通道异常: {type(e).__name__}: {e}"}
    finally:
        _pending.pop(rpc_id, None)


# ── WS 端点 ───────────────────────────────────────────────────

async def phone_endpoint(ws: WebSocket, auth_ok: bool) -> None:
    """FastAPI websocket 路由体（server.py 注册，鉴权由其完成）。

    Args:
        ws: 已 accept 的连接。
        auth_ok: 鉴权结果（失败时 server.py 直接 close，不进这里）。
    """
    global _phone_ws, _phone_meta, _last_heartbeat

    async with _phone_lock:
        # 单手机策略：新连接顶替旧连接（重连场景）
        if _phone_ws is not None and _phone_ws is not ws:
            try:
                await _phone_ws.close(code=4000, reason="被新连接顶替")
            except Exception:
                pass
        _phone_ws = ws
        _phone_meta = {}
    _last_heartbeat = time.time()

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            rid = data.get("id")

            if rid is None:
                # 通知类消息（hello / screen_changed / pong）
                mtype = data.get("type", "")
                if mtype == "hello":
                    _phone_meta = {"device": data.get("device", "")}
                    _last_heartbeat = time.time()
                elif mtype == "screen_changed":
                    # M1 仅记录；M2 决策循环可用作"屏幕变了，该重拉"信号
                    _phone_meta["last_package"] = data.get("package", "")
                    _last_heartbeat = time.time()
                elif mtype == "pong":
                    _last_heartbeat = time.time()
                continue

            # RPC 响应——唤醒对应 Future
            fut = _pending.get(int(rid))
            if fut is not None and not fut.done():
                if "error" in data:
                    fut.set_result({"error": str(data["error"])[:200]})
                else:
                    fut.set_result(data.get("result", {}))

    except Exception:
        # 断开（WebSocketDisconnect 等）
        pass
    finally:
        async with _phone_lock:
            if _phone_ws is ws:
                _phone_ws = None
                _phone_meta = {}
