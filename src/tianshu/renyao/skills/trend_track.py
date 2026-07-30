"""趋势追踪 Skill — GitHub Trending 监控 + 科技新闻采集。

三爻分类: 地（数据获取）
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .base import BaseSkill, SkillTool


class TrendTrackSkill(BaseSkill):
    name = "trend-track"
    description = "监控 GitHub Trending、科技新闻，按周归档到趋势追踪目录"
    trigram = "地"
    trigger_keywords = [
        "趋势", "trending", "github", "GitHub", "科技新闻",
        "每周", "本周", "热点", "趋势追踪",
    ]

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="save_trend_report",
                description="保存趋势报告到 趋势追踪/科技新闻/YYYY.MM/WX/ 目录。",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "报告标题"},
                        "content": {"type": "string", "description": "报告内容（Markdown）"},
                        "category": {
                            "type": "string",
                            "enum": ["科技新闻", "军事科技", "时政", "研究论文"],
                            "description": "报告分类",
                            "default": "科技新闻",
                        },
                        "date": {"type": "string", "description": "报告日期 YYYY-MM-DD，默认今天"},
                    },
                    "required": ["title", "content"],
                },
                handler=self._save_report,
                permission_level=2,  # WRITE
            ),
            SkillTool(
                name="get_weekly_summary",
                description="查看本周/指定周的目录中有哪些报告。",
                parameters={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["科技新闻", "军事科技", "时政", "研究论文"],
                            "description": "报告分类",
                        },
                        "week_offset": {
                            "type": "integer",
                            "description": "周偏移：0=本周，-1=上周",
                            "default": 0,
                        },
                    },
                    "required": ["category"],
                },
                handler=self._weekly_summary,
            ),
        ]

    # ── 工具实现 ─────────────────────────────────────────────────────

    async def _save_report(
        self, title: str, content: str,
        category: str = "科技新闻", date: str = "",
    ) -> str:
        """保存趋势报告。"""
        if date:
            d = datetime.strptime(date, "%Y-%m-%d")
        else:
            d = datetime.now()

        month_dir = f"{d.year}.{d.month:02d}"
        week_num = d.isocalendar()[1]
        first_week = d.replace(day=1).isocalendar()[1]
        month_week = week_num - first_week + 1

        # 计算本周一的日期
        weekday = d.weekday()
        monday = d.replace(day=d.day - weekday)
        sunday = monday.replace(day=monday.day + 6)
        w = f"W{month_week}_{monday.month:02d}.{monday.day:02d}-{sunday.month:02d}.{sunday.day:02d}"

        base = Path(f"F:/趋势追踪/{category}/{month_dir}/{w}")
        base.mkdir(parents=True, exist_ok=True)

        safe_title = title.replace("/", "-").replace("\\", "-")[:60]
        file_path = base / f"{safe_title}_{d.strftime('%Y-%m-%d')}.md"
        file_path.write_text(content, encoding="utf-8")
        return f"报告已保存: {file_path}"

    async def _weekly_summary(self, category: str, week_offset: int = 0) -> str:
        """查看目录内容。"""
        d = datetime.now()
        month_dir = f"{d.year}.{d.month:02d}"

        base = Path(f"F:/趋势追踪/{category}/{month_dir}")
        if not base.exists():
            return f"目录不存在: {base}"

        weeks = sorted([p.name for p in base.iterdir() if p.is_dir()])
        if not weeks:
            return f"{category}/{month_dir}/ 下暂无报告"

        lines = [f"{category}/{month_dir}/:"]
        for w in weeks:
            wdir = base / w
            files = list(wdir.glob("*"))
            lines.append(f"  {w}/ ({len(files)} 个文件)")
            for f in sorted(files)[:5]:
                lines.append(f"    - {f.name}")
        return "\n".join(lines)
