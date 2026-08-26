"""TS-018 M1 测试 — phone_ws 协议层 + phone skill 格式化（mock WS）。"""

import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root / "src"))

import pytest

from tianshu.renyao.skills.phone import PhoneSkill


# ── 节点格式化（纯函数级）──────────────────────────────────


def _mk_nodes():
    return {
        "package": "com.miui.home",
        "nodes": [
            {"text": "设置", "desc": "", "bounds": [80, 900, 200, 990],
             "clickable": True, "scrollable": False},
            {"text": "", "desc": "搜索框", "bounds": [0, 100, 1080, 200],
             "clickable": True, "scrollable": False},
            {"text": "微信", "desc": "", "bounds": [0, 1200, 270, 1420],
             "clickable": True, "scrollable": False},
        ],
    }


class TestFormatNodes:
    def test_basic_render(self):
        out = PhoneSkill._format_nodes(_mk_nodes())
        assert "com.miui.home" in out
        assert "设置" in out and "微信" in out
        # 中心坐标可 tap：设置 bounds [80,900,200,990] → @(140,945)
        assert "@(140,945)" in out
        # 可点标记
        assert "[可点]" in out

    def test_desc_as_label(self):
        out = PhoneSkill._format_nodes(_mk_nodes())
        assert "搜索框" in out  # 无 text 用 desc

    def test_empty_nodes(self):
        out = PhoneSkill._format_nodes({"package": "com.game", "nodes": []})
        assert "自绘界面" in out or "无可读节点" in out


# ── skill 注册 ─────────────────────────────────────────────


class TestSkillRegistration:
    def test_skill_metadata(self):
        s = PhoneSkill()
        assert s.name == "phone"
        assert s.trigram == "地"  # 地曜——感知/执行

    def test_screen_state_tool_schema(self):
        s = PhoneSkill()
        tools = s.get_tools()
        assert len(tools) >= 1
        t = tools[0]
        assert t.name == "screen_state"
        assert callable(t.handler)


# ── phone_ws RPC 层（mock 连接）────────────────────────────


class TestPhoneRpc:
    def test_rpc_when_disconnected(self):
        from tianshu.gateway import phone_ws
        phone_ws._phone_ws = None
        result = asyncio.run(phone_ws.phone_rpc("screen_state"))
        assert "error" in result
        assert "未连接" in result["error"]

    def test_rpc_roundtrip(self):
        """mock 手机连接：发 RPC → 模拟手机回 result → Future 唤醒。"""
        from tianshu.gateway import phone_ws

        class _FakeWS:
            def __init__(self):
                self.sent = []

            async def send_text(self, payload: str):
                self.sent.append(payload)
                # 解析请求，回填 result（模拟手机立刻响应）
                import json as _json
                req = _json.loads(payload)
                rid = req["id"]

                async def _respond():
                    await asyncio.sleep(0.01)
                    fut = phone_ws._pending.get(rid)
                    if fut and not fut.done():
                        fut.set_result({"package": "com.test", "nodes": []})

                asyncio.get_event_loop().create_task(_respond())

        fake = _FakeWS()
        phone_ws._phone_ws = fake
        try:
            result = asyncio.run(phone_ws.phone_rpc("screen_state", timeout=2.0))
            assert result == {"package": "com.test", "nodes": []}
            assert len(fake.sent) == 1
        finally:
            phone_ws._phone_ws = None
            phone_ws._pending.clear()

    def test_status_reflects_connection(self):
        from tianshu.gateway import phone_ws
        phone_ws._phone_ws = None
        assert phone_ws.phone_status()["connected"] is False


# ── phone endpoint 消息分发（hello/screen_changed/响应）────


class TestEndpointDispatch:
    def _drive(self, messages: list[dict]):
        """喂消息序列给 phone_endpoint，观察副作用。"""
        from tianshu.gateway import phone_ws

        class _FakeWS:
            def __init__(self):
                self._queue = asyncio.Queue()
                for m in messages:
                    self._queue.put_nowait(m)

            async def receive_text(self):
                import json as _json
                m = await self._queue.get()
                if m is None:
                    raise RuntimeError("disconnected")
                return _json.dumps(m)

        async def run():
            ws = _FakeWS()
            await phone_ws.phone_endpoint(ws, auth_ok=True)

        asyncio.run(run())

    def test_hello_sets_meta(self):
        from tianshu.gateway import phone_ws
        self._drive([
            {"type": "hello", "device": "xiaomi-17"},
            None,
        ])
        # 断开后 meta 清空，但运行中曾设置——此处只验证不炸+清理
        assert phone_ws.phone_status()["connected"] is False

    def test_rpc_response_wakes_future(self):
        from tianshu.gateway import phone_ws

        class _FakeWS:
            def __init__(self, messages):
                import asyncio as _a
                self._queue = _a.Queue()
                for m in messages:
                    self._queue.put_nowait(m)

            async def receive_text(self):
                import json as _json
                m = await self._queue.get()
                if m is None:
                    raise RuntimeError("disconnected")
                return _json.dumps(m)

        async def run():
            fut = asyncio.get_event_loop().create_future()
            phone_ws._pending[42] = fut
            try:
                await phone_ws.phone_endpoint(
                    _FakeWS([{"id": 42, "result": {"ok": True}}, None]),
                    auth_ok=True,
                )
                assert fut.done()
                assert fut.result() == {"ok": True}
            finally:
                phone_ws._pending.clear()

        asyncio.run(run())
