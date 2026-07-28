"""Plugin 系统 — 用户自定义扩展钩子。

插件放在 ~/.tianshu/plugins/{name}/plugin.py，
实现 on_startup / on_message / on_shutdown 三个钩子。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _plugins_dir() -> Path:
    return Path.home() / ".tianshu" / "plugins"


class PluginManager:
    """加载和管理用户插件。"""

    def __init__(self):
        self._plugins: list[dict[str, Any]] = []

    def discover(self) -> list[str]:
        """扫描 ~/.tianshu/plugins/ 下的所有插件目录。"""
        names = []
        d = _plugins_dir()
        if not d.exists():
            return names
        for item in sorted(d.iterdir()):
            if item.is_dir() and (item / "plugin.py").exists():
                names.append(item.name)
        return names

    def load(self, name: str) -> bool:
        """加载一个插件。"""
        path = _plugins_dir() / name / "plugin.py"
        if not path.exists():
            return False
        try:
            spec = importlib.util.spec_from_file_location(
                f"tianshu_plugin_{name}", str(path)
            )
            if spec is None or spec.loader is None:
                return False
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            self._plugins.append({
                "name": name,
                "module": mod,
                "on_startup": getattr(mod, "on_startup", None),
                "on_message": getattr(mod, "on_message", None),
                "on_shutdown": getattr(mod, "on_shutdown", None),
            })
            return True
        except Exception:
            return False

    def load_all(self) -> int:
        """加载所有发现的插件。返回成功数。"""
        count = 0
        for name in self.discover():
            if self.load(name):
                count += 1
        return count

    async def fire_startup(self, core) -> None:
        """触发所有插件的 on_startup。"""
        for p in self._plugins:
            if p["on_startup"]:
                try:
                    result = p["on_startup"](core)
                    if hasattr(result, "__await__"):
                        await result
                except Exception:
                    pass

    async def fire_message(self, ctx) -> str | None:
        """触发 on_message 链。返回第一个非 None 的结果。"""
        for p in self._plugins:
            if p["on_message"]:
                try:
                    result = p["on_message"](ctx)
                    if hasattr(result, "__await__"):
                        result = await result
                    if result is not None:
                        return str(result)
                except Exception:
                    pass
        return None

    async def fire_shutdown(self) -> None:
        for p in self._plugins:
            if p["on_shutdown"]:
                try:
                    result = p["on_shutdown"]()
                    if hasattr(result, "__await__"):
                        await result
                except Exception:
                    pass

    @property
    def count(self) -> int:
        return len(self._plugins)

    def list_plugins(self) -> list[dict[str, str]]:
        return [{"name": p["name"],
                 "hooks": [h for h in ("on_startup","on_message","on_shutdown")
                          if p[h] is not None]}
                for p in self._plugins]
