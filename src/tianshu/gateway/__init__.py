"""Platform Gateway — FastAPI HTTP 服务 + CLI 客户端。"""

from tianshu.gateway.cli import StreamRenderer, chat_once, render_splash_rich

__all__ = ["StreamRenderer", "chat_once", "render_splash_rich"]
