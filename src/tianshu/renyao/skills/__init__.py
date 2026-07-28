"""Skills 系统 — 地爻/人爻/天爻的能力封装。

内置 Skills（Python 类）:
  - paper_radar  — 论文搜索/下载/分类
  - trend_track  — 趋势追踪
  - code_assist  — 代码助手

用户 Skills（SKILL.md）:
  ~/.tianshu/skills/*.md — 自进化生成
"""

from .base import BaseSkill, SkillDef, SkillTool
from .loader import SkillLoader
from .executor import SkillExecutor
from .observer import SkillObserver

__all__ = [
    "BaseSkill", "SkillDef", "SkillTool",
    "SkillLoader", "SkillExecutor", "SkillObserver",
]
