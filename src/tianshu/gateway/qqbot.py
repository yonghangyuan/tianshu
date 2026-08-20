"""QQ Bot 适配器 — 官方 QQ 开放平台 API。

需要: https://q.qq.com/ → 创建机器人 → 获取 AppID + Token
文档: https://bot.q.qq.com/wiki/

用法:
    tianshu-qqbot --app-id xxx --token xxx
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from tianshu.sdk.models import AgentRequest
from tianshu.core.service import AgentCore
from tianshu.core.config import load_providers, load_routing_config
from tianshu.core.setup import load_user_keys


class QQBot:
    """QQ 官方 Bot — WebSocket 连接。

    支持:
      - 单聊 (C2C)
      - 群聊 @机器人 (GROUP_AT)
      - 频道消息
    """

    BASE = "https://api.sgroup.qq.com"

    def __init__(self, app_id: str = "", token: str = "", secret: str = "", core: AgentCore | None = None):
        self._app_id = app_id or os.environ.get("QQ_BOT_APP_ID", "")
        self._token = token or os.environ.get("QQ_BOT_TOKEN", "")
        self._secret = secret or os.environ.get("QQ_BOT_SECRET", "")
        self._core = core
        self._access_token = ""
        self._token_expiry = 0.0
        self._ws = None
        self._session_counter = 0

    async def _get_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://bots.qq.com/app/getAppAccessToken",
                json={"appId": self._app_id, "clientSecret": self._secret},
            )
            data = resp.json()
            self._access_token = data.get("access_token", "")
            self._token_expiry = time.time() + data.get("expires_in", 7200) - 300
            return self._access_token

    async def send_message(self, channel_id: str, text: str, msg_id: str = "") -> dict:
        """发送消息到频道/群聊。"""
        token = await self._get_token()
        async with httpx.AsyncClient() as client:
            body = {
                "content": text[:2000],
                "msg_id": msg_id,
            }
            resp = await client.post(
                f"{self.BASE}/v2/channels/{channel_id}/messages",
                headers={
                    "Authorization": f"QQBot {self._access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            return resp.json()

    async def handle_message(self, channel_id: str, user_id: str, text: str) -> str:
        """处理消息 → AgentCore → 返回回复。"""
        if self._core is None:
            return "AgentCore 未初始化。"

        self._session_counter += 1
        resp = await self._core.run(AgentRequest(
            input=text,
            session_id=f"qq_{channel_id}_{user_id}",
        ))
        return resp.content or resp.error or "(无响应)"

    async def start_ws(self, loop=True) -> None:
        """WebSocket 连接 QQ Bot 网关 (开发测试用)。"""
        token = await self._get_token()
        ws_url = f"wss://api.sgroup.qq.com/websocket"
        print(f"QQ Bot WebSocket → {ws_url}")

        # 获取网关 URL
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.sgroup.qq.com/gateway",
                headers={"Authorization": f"QQBot {self._access_token}"},
            )
            data = resp.json()
            ws_url = data.get("url", ws_url)

        import websockets
        async with websockets.connect(ws_url) as ws:
            self._ws = ws
            print(f"QQ Bot 已连接")
            async for raw in ws:
                event = json.loads(raw)
                if event.get("op") == 10:  # Hello
                    # 鉴权
                    await ws.send(json.dumps({
                        "op": 2,
                        "d": {
                            "token": f"QQBot {self._access_token}",
                            "intents": 1 | 512,  # PUBLIC_GUILD_MESSAGES | DIRECT_MESSAGE
                            "shard": [0, 1],
                        },
                    }))
                elif event.get("op") == 0:  # Dispatch
                    d = event.get("d", {})
                    content = d.get("content", "")
                    channel_id = d.get("channel_id", "")
                    author = d.get("author", {})
                    user_id = author.get("id", "")
                    msg_id = d.get("id", "")

                    if content and channel_id:
                        reply = await self.handle_message(channel_id, user_id, content)
                        await self.send_message(channel_id, reply, msg_id)

    async def start_poll(self, interval: int = 3) -> None:
        """轮询模式——不需要 WebSocket，开发测试用。"""
        print(f"QQ Bot 轮询模式 (间隔 {interval}s)")
        while True:
            try:
                token = await self._get_token()
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{self.BASE}/v2/users/@me/messages",
                        headers={"Authorization": f"QQBot {token}"},
                    )
                    # 轮询模式需要公网回调或 WebSocket，这里是简化版
                    pass
            except Exception as e:
                print(f"Poll error: {e}")
            await asyncio.sleep(interval)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--secret", default="")
    parser.add_argument("--mode", choices=["ws", "http"], default="http")
    parser.add_argument("--port", type=int, default=8722)
    args = parser.parse_args()

    from tianshu.core.config import resolve_config_dir
    config_dir = resolve_config_dir(_project_root)
    user_keys = load_user_keys()
    registry = load_providers(config_dir / "providers.yaml", extra_keys=user_keys)
    routing = load_routing_config(config_dir / "providers.yaml")
    soul = (config_dir / "soul.md").read_text(encoding="utf-8") if (config_dir / "soul.md").exists() else ""
    core = AgentCore()
    core.setup(registry=registry, routing=routing, system_prompt=soul,
               db_path=str(_project_root / "tianshu.db"), skill_discover=True)

    bot = QQBot(app_id=args.app_id, token=args.token, secret=args.secret, core=core)
    print(f"QQ Bot 就绪 (mode={args.mode})")

    if args.mode == "ws":
        asyncio.run(bot.start_ws())
    else:
        from fastapi import FastAPI, Request
        app = FastAPI()

        @app.post("/qq/callback")
        async def callback(request: Request):
            body = await request.json()
            content = body.get("content", "")
            channel_id = body.get("channel_id", "")
            user_id = body.get("author", {}).get("id", "")
            msg_id = body.get("id", "")
            if content and channel_id:
                reply = await bot.handle_message(channel_id, user_id, content)
                await bot.send_message(channel_id, reply, msg_id)
            return {"code": 0}

        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
