"""Vision Skill — 多模态图像分析。

支持调用 GLM-5V / Qwen-VL 等多模态模型分析图像。
用于地图识别、卫星图解读、场景理解等。
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx

from .base import BaseSkill, SkillTool


class VisionSkill(BaseSkill):
    name = "vision"
    description = "多模态图像分析——分析图片、地图、卫星图、截图等。支持本地路径和 URL。"
    trigram = "地"
    trigger_keywords = ["看图", "识别", "分析图片", "vision", "图像"]

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="vision",
                description=(
                    "Analyze an image using a multimodal vision model (GLM-5V).\n"
                    "Use for: reading maps, analyzing satellite imagery, understanding diagrams, OCR.\n"
                    "Do NOT use for: text-only questions (use web_search instead)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "image": {
                            "type": "string",
                            "description": "Image path or URL to analyze. Supports local paths and http/https URLs.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "What to analyze in the image. E.g. 'Describe this map', 'Read the text', 'Find all buildings'",
                        },
                    },
                    "required": ["image", "prompt"],
                },
                handler=self._analyze,
                permission_level=0,
            )
        ]

    async def _analyze(self, image: str, prompt: str, **kwargs) -> str:
        """调用 GLM-5V 分析图像。"""
        import os

        # 获取图像 base64
        image_b64 = ""
        if image.startswith("http://") or image.startswith("https://"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(image)
                resp.raise_for_status()
                image_b64 = base64.b64encode(resp.content).decode()
        elif Path(image).exists():
            image_b64 = base64.b64encode(Path(image).read_bytes()).decode()
        else:
            return f"❌ 图像文件不存在: {image}"

        # 调用 GLM-5V (智谱)
        api_key = os.environ.get("ZHIPU_API_KEY", "")
        if not api_key:
            # 尝试从配置文件加载
            try:
                from tianshu.core.setup import load_user_keys
                keys = load_user_keys()
                api_key = keys.get("zhipu", "")
            except Exception:
                pass

        if not api_key:
            return "❌ 未配置 ZHIPU_API_KEY。请设置环境变量或在 /setup 中配置。"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "glm-5v-turbo",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                                },
                            ],
                        }
                    ],
                    "max_tokens": 1024,
                },
            )
            if resp.status_code != 200:
                return f"❌ GLM-5V 调用失败: HTTP {resp.status_code} — {resp.text[:200]}"

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content or "⚠️ 模型返回空内容"
