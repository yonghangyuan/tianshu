"""企业微信 Bot — 接收消息 → AgentCore → 回复。

需要: 企业微信管理后台 → 应用管理 → 创建应用 → 获取 CorpID/AgentID/Secret
文档: https://developer.work.weixin.qq.com/

用法:
    tianshu-wechat --corp-id xxx --agent-id 1000001 --secret xxx
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import xml.etree.ElementTree as ET

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from tianshu.sdk.models import AgentRequest
from tianshu.core.service import AgentCore
from tianshu.core.config import load_providers, load_routing_config
from tianshu.core.setup import load_user_keys


class WeChatBot:
    """企业微信 Bot。

    两种模式:
      - Webhook 回调 (需要公网 IP) — 生产用
      - 轮询 (polling) — 开发测试用
    """

    def __init__(self, corp_id: str = "", agent_id: str = "", secret: str = "",
                 token: str = "", aes_key: str = "", core: AgentCore | None = None):
        self._corp_id = corp_id or os.environ.get("WECHAT_CORP_ID", "")
        self._agent_id = agent_id or os.environ.get("WECHAT_AGENT_ID", "")
        self._secret = secret or os.environ.get("WECHAT_SECRET", "")
        self._token = token or os.environ.get("WECHAT_TOKEN", "")
        self._aes_key = aes_key or os.environ.get("WECHAT_AES_KEY", "")
        self._core = core
        self._access_token = ""
        self._token_expiry = 0.0

    async def _get_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": self._corp_id, "corpsecret": self._secret},
            )
            data = resp.json()
            self._access_token = data.get("access_token", "")
            self._token_expiry = time.time() + data.get("expires_in", 7200) - 300
            return self._access_token

    async def send_message(self, user_id: str, text: str) -> dict:
        """发送文本消息到指定用户。"""
        token = await self._get_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                json={
                    "touser": user_id,
                    "msgtype": "text",
                    "agentid": int(self._agent_id),
                    "text": {"content": text[:2000]},
                },
            )
            return resp.json()

    async def handle_message(self, user_id: str, text: str) -> str:
        """处理消息 → AgentCore → 返回回复文本。"""
        if self._core is None:
            return "AgentCore 未初始化。"
        resp = await self._core.run(AgentRequest(
            input=text, session_id=f"wechat_{user_id}"
        ))
        return resp.content or resp.error or "(无响应)"

    # ── Webhook 回调 (FastAPI 集成) ──

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """URL 验证——企业微信后台配置回调 URL 时调用。"""
        # 简化验证——生产环境需要 WXBizMsgCrypt
        return echostr

    def decrypt_message(self, xml_body: str) -> dict:
        """解密企业微信推送的消息——生产环境需要 WXBizMsgCrypt。"""
        root = ET.fromstring(xml_body)
        return {
            "user_id": root.findtext("FromUserName", ""),
            "content": root.findtext("Content", ""),
            "msg_type": root.findtext("MsgType", "text"),
        }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--corp-id", default="")
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--secret", default="")
    parser.add_argument("--port", type=int, default=8721)
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

    bot = WeChatBot(corp_id=args.corp_id, agent_id=args.agent_id, secret=args.secret, core=core)
    print(f"企业微信 Bot 就绪 (port {args.port})")
    print("配置回调 URL: http://YOUR_IP:{args.port}/wechat/callback")

    # 直接复用 FastAPI server 的端口——这里给一个独立入口
    from fastapi import FastAPI, Request
    app = FastAPI()

    @app.get("/wechat/callback")
    async def verify(msg_signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""):
        return bot.verify_url(msg_signature, timestamp, nonce, echostr)

    @app.post("/wechat/callback")
    async def callback(request: Request):
        body = await request.body()
        msg = bot.decrypt_message(body.decode())
        reply = await bot.handle_message(msg["user_id"], msg["content"])
        await bot.send_message(msg["user_id"], reply)
        return {"errcode": 0}

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
