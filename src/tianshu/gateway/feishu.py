"""飞书 Bot 适配器——接收消息 → AgentCore → 回复。

用法:
    tianshu-feishu --app-id xxx --app-secret xxx

飞书开放平台配置:
    1. 创建企业自建应用 → 机器人 → 启用
    2. 事件订阅 → 添加 im.message.receive_v1
    3. 权限: im:message, im:message:send_as_bot
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from tianshu.sdk.models import AgentRequest
from tianshu.core.service import AgentCore
from tianshu.core.config import load_providers, load_routing_config
from tianshu.core.setup import load_user_keys


class FeishuBot:
    """飞书 Bot——轮询 + Webhook 双模式。

    轮询模式: 无需公网 IP，适合开发测试。
    Webhook 模式: 需要公网回调地址，适合生产。
    """

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        core: AgentCore | None = None,
    ):
        self._app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self._app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self._core = core
        self._token: str = ""
        self._token_expiry: float = 0

    async def _get_token(self) -> str:
        """获取 tenant_access_token。"""
        if self._token and time.time() < self._token_expiry:
            return self._token
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            data = resp.json()
            self._token = data.get("tenant_access_token", "")
            self._token_expiry = time.time() + data.get("expire", 3600) - 60
            return self._token

    async def send_message(self, chat_id: str, text: str) -> dict:
        """发送消息到指定会话。"""
        token = await self._get_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
            )
            return resp.json()

    async def handle_message(self, chat_id: str, text: str) -> str:
        """处理一条消息——调 AgentCore 并返回回复文本。"""
        if self._core is None:
            return "AgentCore not initialized."
        resp = await self._core.run(AgentRequest(
            input=text, session_id=f"feishu_{chat_id}"
        ))
        return resp.content or resp.error or "(no response)"

    async def poll_messages(self, interval: int = 3) -> None:
        """轮询模式——每隔 N 秒拉取新消息。

        开发测试用。生产环境用 Webhook 回调。
        """
        print(f"飞书 Bot 轮询模式启动 (间隔 {interval}s)")
        last_msg_id = ""
        while True:
            try:
                token = await self._get_token()
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://open.feishu.cn/open-apis/im/v1/messages",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"page_size": 5, "sort_type": "ByCreateTimeDesc"},
                    )
                    items = resp.json().get("data", {}).get("items", [])
                    for item in items:
                        msg_id = item.get("message_id", "")
                        if msg_id == last_msg_id:
                            break
                        content = json.loads(item.get("content", "{}"))
                        text = content.get("text", "")
                        chat_id = item.get("chat_id", "")
                        if text and chat_id:
                            reply = await self.handle_message(chat_id, text)
                            await self.send_message(chat_id, reply)
                    if items:
                        last_msg_id = items[0].get("message_id", "")
            except Exception as e:
                print(f"Poll error: {e}")
            await asyncio.sleep(interval)


def main():
    """CLI 入口: tianshu-feishu"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", default="")
    parser.add_argument("--app-secret", default="")
    parser.add_argument("--interval", type=int, default=3)
    parser.add_argument("--port", type=int, default=8720,
                       help="AgentCore HTTP port (if using remote core)")
    args = parser.parse_args()

    # 初始化 AgentCore
    config_dir = _project_root / "config"
    user_keys = load_user_keys()
    registry = load_providers(config_dir / "providers.yaml", extra_keys=user_keys)
    routing = load_routing_config(config_dir / "providers.yaml")
    soul = (config_dir / "soul.md").read_text(encoding="utf-8") if (config_dir / "soul.md").exists() else ""
    core = AgentCore()
    core.setup(registry=registry, routing=routing, system_prompt=soul,
               db_path=str(_project_root / "tianshu.db"), skill_discover=True)

    bot = FeishuBot(app_id=args.app_id, app_secret=args.app_secret, core=core)
    asyncio.run(bot.poll_messages(args.interval))


if __name__ == "__main__":
    main()
