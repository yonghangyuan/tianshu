"""Phone Skill — 手机控制工具（TS-018 M1）。

手机是哑终端（docs/ANDROID_CONTROL_PLAN.md D4）：本 skill 只是把
phone_rpc 的结果整理成 LLM 可读文本。决策在模型，闸门在 AgentCore。

M1: screen_state；M2 增补 tap/input_text/scroll/nav。
"""

from __future__ import annotations

from .base import BaseSkill, SkillTool


class PhoneSkill(BaseSkill):
    name = "phone"
    description = (
        "操作真实 Android 手机（小米17）——读屏幕、点按、输入、滚动、导航。\n"
        "手机经 WebSocket 连到本机天枢；读屏幕返回当前界面的控件列表\n"
        "（文字/描述/坐标/可点击），这是你'看'手机的方式。"
    )
    trigram = "地"
    trigger_keywords = ["手机", "phone", "屏幕", "调亮度"]

    def get_tools(self) -> list[SkillTool]:
        tools = [SkillTool(
            name="screen_state",
            description=(
                "Read the current Android phone screen. Returns the active app "
                "package and a flat list of UI nodes (text/desc/bounds/clickable/"
                "scrollable). Use this to see what is on screen before tapping."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=self._screen_state,
        )]
        return tools

    # ── 工具实现 ─────────────────────────────────────────────

    async def _screen_state(self, **kwargs) -> str:
        from tianshu.gateway.phone_ws import phone_rpc
        result = await phone_rpc("screen_state", timeout=10.0)
        if "error" in result:
            return f"⚠️ {result['error']}"
        return self._format_nodes(result)

    # ── 格式化 ───────────────────────────────────────────────

    @staticmethod
    def _format_nodes(state: dict) -> str:
        """节点树 → LLM 友好的紧凑文本。

        每行: [clickable] text (desc) @x,y —— 坐标给中心点，直接可 tap。
        """
        pkg = state.get("package", "?")
        nodes = state.get("nodes", [])
        if not nodes:
            return f"包名: {pkg}\n(无可读节点——可能是自绘界面/锁屏)"
        lines = [f"包名: {pkg}", f"节点: {len(nodes)} 个", ""]
        for n in nodes[:200]:
            text = n.get("text", "")
            desc = n.get("desc", "")
            bounds = n.get("bounds", [0, 0, 0, 0])
            cx = (bounds[0] + bounds[2]) // 2
            cy = (bounds[1] + bounds[3]) // 2
            flags = []
            if n.get("clickable"):
                flags.append("可点")
            if n.get("scrollable"):
                flags.append("可滚")
            tag = f"[{'/'.join(flags)}]" if flags else ""
            label = text or desc
            lines.append(f"{tag} {label} @({cx},{cy})")
        return "\n".join(lines)
