"""Skill Manifest — 静态声明，延迟加载。

借鉴 OpenClaw: PluginManifest 是廉价 JSON，Runtime 按需导入。
SkillLoader 扫描 manifest 不导入 .py，启动速度不受 Skill 数量影响。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any


def _builtin_skills_dir() -> Path:
    return Path(__file__).resolve().parent


def generate_manifests() -> list[Path]:
    """从现有 Python Skill 类生成 skill.json manifest。

    扫描 renayo/skills/*.py，找到继承 BaseSkill 的类，
    实例化后导出其元数据为 JSON manifest。
    已有 manifest 的跳过。
    """
    import ast
    import inspect

    skills_dir = _builtin_skills_dir()
    generated: list[Path] = []

    for py_file in sorted(skills_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        manifest_path = py_file.with_suffix(".json")
        if manifest_path.exists():
            # 已有 manifest——但如果 .py 比 .json 新，重新生成
            if py_file.stat().st_mtime < manifest_path.stat().st_mtime:
                generated.append(manifest_path)
                continue

        # AST 解析找类名
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        skill_class = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if getattr(base, "id", "") in ("BaseSkill",):
                        skill_class = node.name
                        break

        if not skill_class:
            continue

        # 导入并实例化（仅生成 manifest 时）
        module_name = f"tianshu.renyao.skills.{py_file.stem}"
        try:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, skill_class)
            if inspect.isclass(cls):
                inst = cls()
                tools = []
                for t in inst.get_tools():
                    tools.append({
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                        "permission": getattr(t, "permission_level", 0),
                    })
                manifest = {
                    "name": inst.name,
                    "description": inst.description,
                    "trigram": inst.trigram,
                    "version": 1,
                    "tools": tools,
                    "trigger_keywords": inst.trigger_keywords,
                    "handler": f"{module_name}:{skill_class}",
                }
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                generated.append(manifest_path)
        except Exception:
            pass

    return generated


def load_manifest(manifest_path: Path) -> dict[str, Any] | None:
    """从 JSON manifest 加载 Skill 元数据（不导入 Python 模块）。"""
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_handler(handler_ref: str) -> Any | None:
    """延迟加载 Skill handler。

    Args:
        handler_ref: "module.path:ClassName" 格式

    Returns:
        Skill 实例或 None
    """
    if ":" not in handler_ref:
        return None
    try:
        module_path, class_name = handler_ref.split(":", 1)
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls()
    except Exception:
        return None


def discover_all_manifests() -> list[dict[str, Any]]:
    """扫描所有 skill.json，返回元数据列表（不导入 Python 模块）。"""
    skills_dir = _builtin_skills_dir()
    manifests: list[dict[str, Any]] = []

    for json_file in sorted(skills_dir.glob("*.json")):
        if json_file.name.startswith("_"):
            continue
        data = load_manifest(json_file)
        if data and data.get("name"):
            data["_manifest_path"] = str(json_file)
            manifests.append(data)

    return manifests
