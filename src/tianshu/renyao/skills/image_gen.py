"""Image Gen Skill — 图片生成与描述。"""

from .base import BaseSkill, SkillTool


class ImageGenSkill(BaseSkill):
    name = "image-gen"
    description = "生成图片描述提示词，可视化数据/架构/流程图"
    trigram = "人"
    trigger_keywords = ["画", "图", "图片", "生成图", "可视化", "流程图", "架构图", "思维导图"]

    def get_tools(self) -> list[SkillTool]:
        return [SkillTool(
            name="image_prompt",
            description="Generate a detailed image generation prompt. Use for: architecture diagrams, flowcharts, data visualizations, conceptual illustrations.",
            parameters={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What to draw"},
                    "style": {"type": "string", "description": "Style: diagram|illustration|chart|logo", "default": "diagram"},
                },
                "required": ["description"],
            },
            handler=self._generate,
        )]

    async def _generate(self, description: str, style: str = "diagram", **kwargs) -> str:
        styles = {
            "diagram": "clean technical diagram, Chinese labels, dark theme, blue accents, professional",
            "illustration": "detailed illustration, Chinese calligraphy style, ink wash, elegant",
            "chart": "data visualization chart, clean minimal, color-coded, labeled axes",
            "logo": "modern logo design, vector style, simple geometric, Chinese aesthetic",
        }
        s = styles.get(style, styles["diagram"])
        return (
            f"IMAGE_PROMPT: {description}\n"
            f"Style: {s}\n\n"
            f"Use this prompt with any image generation tool (DALL-E, Midjourney, Stable Diffusion).\n"
            f"Prompt: {description}, {s}"
        )
