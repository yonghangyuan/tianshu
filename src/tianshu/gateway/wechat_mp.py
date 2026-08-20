"""微信公众号 Bot — 被动回复 + 客服消息。

文档: https://developers.weixin.qq.com/doc/offiaccount/

用法:
    tianshu-wechat-mp --app-id xxx --app-secret xxx --token xxx
"""

from __future__ import annotations

import asyncio
import hashlib
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


class WeChatMPBot:
    """微信公众号 Bot。

    Hermes 用公众号 OpenAPI 接入微信——我们一样。
    """

    def __init__(self, app_id: str = "", app_secret: str = "", token: str = "",
                 core: AgentCore | None = None):
        self._app_id = app_id or os.environ.get("WECHAT_MP_APP_ID", "")
        self._app_secret = app_secret or os.environ.get("WECHAT_MP_APP_SECRET", "")
        self._verify_token = token or os.environ.get("WECHAT_MP_TOKEN", "")
        self._core = core
        self._access_token = ""
        self._token_expiry = 0.0

    async def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={"grant_type": "client_credential",
                        "appid": self._app_id, "secret": self._app_secret},
            )
            data = resp.json()
            self._access_token = data.get("access_token", "")
            self._token_expiry = time.time() + 5400
            return self._access_token

    # ── URL 验证 (公众号后台配置) ──

    def verify_signature(self, signature: str, timestamp: str, nonce: str) -> bool:
        """验证微信服务器签名。"""
        tmp = sorted([self._verify_token, timestamp, nonce])
        tmp_str = "".join(tmp)
        return hashlib.sha1(tmp_str.encode()).hexdigest() == signature

    # ── 消息处理 ──

    def parse_message(self, xml_body: str) -> dict:
        """解析 XML 消息体。"""
        root = ET.fromstring(xml_body)
        return {
            "from_user": root.findtext("FromUserName", ""),
            "to_user": root.findtext("ToUserName", ""),
            "content": root.findtext("Content", ""),
            "msg_type": root.findtext("MsgType", "text"),
        }

    async def handle_message(self, from_user: str, text: str) -> str:
        """调 AgentCore 处理消息。"""
        if self._core is None:
            return "AgentCore 未初始化"
        resp = await self._core.run(AgentRequest(
            input=text, session_id=f"wxmp_{from_user}"
        ))
        return resp.content or ""

    def build_reply_xml(self, to_user: str, from_user: str, content: str) -> str:
        """构造被动回复 XML。"""
        return (
            "<xml>"
            f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
            f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
            f"<CreateTime>{int(time.time())}</CreateTime>"
            "<MsgType><![CDATA[text]]></MsgType>"
            f"<Content><![CDATA[{content[:600]}]]></Content>"
            "</xml>"
        )

    # ── 客服消息 (主动推送，48h 内有效) ──

    async def send_customer_message(self, to_user: str, text: str) -> dict:
        """发送客服消息（用户互动后 48h 内有效）。"""
        token = await self._get_access_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}",
                json={
                    "touser": to_user,
                    "msgtype": "text",
                    "text": {"content": text[:2000]},
                },
            )
            return resp.json()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", default="")
    parser.add_argument("--app-secret", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--port", type=int, default=8723)
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

    bot = WeChatMPBot(app_id=args.app_id, app_secret=args.app_secret, token=args.token, core=core)

    from fastapi import FastAPI, Request, Response
    app = FastAPI()

    @app.get("/wechat")
    async def verify(signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""):
        if bot.verify_signature(signature, timestamp, nonce):
            return Response(echostr)
        return Response("fail", status_code=403)

    @app.post("/wechat")
    async def callback(request: Request):
        body = await request.body()
        msg = bot.parse_message(body.decode())
        reply = await bot.handle_message(msg["from_user"], msg["content"])
        xml = bot.build_reply_xml(msg["from_user"], msg["to_user"], reply)
        return Response(xml, media_type="application/xml")

    print(f"微信公众号 Bot → http://0.0.0.0:{args.port}/wechat")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
