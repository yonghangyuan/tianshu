"""Schedule Skill — 日程管理。"""

import json
import time
from pathlib import Path
from .base import BaseSkill, SkillTool


class ScheduleSkill(BaseSkill):
    name = "schedule"
    description = "创建、查询、删除日程提醒"
    trigram = "地"
    trigger_keywords = ["日程", "提醒", "schedule", "日历", "安排", "定时"]

    def _schedule_file(self) -> Path:
        d = Path.home() / ".tianshu"
        d.mkdir(exist_ok=True)
        return d / "schedule.json"

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="schedule_add",
                description="Add a calendar event or reminder.",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Event title"},
                        "time": {"type": "string", "description": "ISO time, e.g. 2026-07-28T14:00"},
                        "note": {"type": "string", "description": "Optional note"},
                    },
                    "required": ["title", "time"],
                },
                handler=self._add,
            ),
            SkillTool(
                name="schedule_list",
                description="List upcoming events.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                handler=self._list,
            ),
            SkillTool(
                name="schedule_remove",
                description="Remove an event by title.",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Event title to remove"},
                    },
                    "required": ["title"],
                },
                handler=self._remove,
            ),
        ]

    def _read(self) -> list[dict]:
        f = self._schedule_file()
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return []

    def _write(self, events: list[dict]) -> None:
        self._schedule_file().write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _add(self, title: str, time: str, note: str = "") -> str:
        events = self._read()
        events.append({"title": title, "time": time, "note": note, "created": time.time()})
        events.sort(key=lambda e: e["time"])
        self._write(events)
        return f"Event added: {title} @ {time}"

    async def _list(self) -> str:
        events = self._read()
        if not events:
            return "No upcoming events."
        now = time.strftime("%Y-%m-%dT%H:%M")
        upcoming = [e for e in events if e["time"] >= now]
        return "\n".join(f"- {e['time']} | {e['title']}" for e in (upcoming or events[-5:]))

    async def _remove(self, title: str) -> str:
        events = self._read()
        before = len(events)
        events = [e for e in events if e["title"] != title]
        self._write(events)
        return f"Removed {before - len(events)} event(s)."
