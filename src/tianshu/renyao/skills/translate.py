"""Translation Skill — 中英互译。"""

from .base import BaseSkill, SkillTool


class TranslateSkill(BaseSkill):
    name = "translate"
    description = "中英互译，保持语义和风格"
    trigram = "人"
    trigger_keywords = ["翻译", "translate", "译", "用英文", "用中文"]

    def get_tools(self) -> list[SkillTool]:
        return [SkillTool(
            name="translate",
            description="Translate text between Chinese and English. Auto-detect source language.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to translate"},
                    "target": {"type": "string", "enum": ["zh", "en"], "description": "Target language", "default": "zh"},
                },
                "required": ["text"],
            },
            handler=self._translate,
        )]

    async def _translate(self, text: str, target: str = "zh") -> str:
        # Translation is done by the LLM itself — this tool just marks the intent
        source = "en" if target == "zh" else "zh"
        return f"TRANSLATE_REQUEST: {source}→{target}\n\n{text}\n\nPlease translate the above text to {'Chinese' if target == 'zh' else 'English'}."
