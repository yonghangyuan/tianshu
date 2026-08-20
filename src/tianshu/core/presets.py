"""Agent 预设 — standard / minimal / code(PTC) 三维度配置，presets.yaml 可配置。

与 normal/auto/plan 会话模式正交：
  - 会话模式管「确认策略」（normal 逐次确认 / auto 免确认 / plan 只读）
  - 预设管「工具集 + 循环行为」（standard 全量 / minimal 四工具 / code 加 run_code 编程入口）

配置化（2026-08-20）：内置三预设为代码默认，用户可经 YAML 覆盖/自定义——
  项目级 config/presets.yaml + 用户级 ~/.tianshu/presets.yaml（优先）。
  YAML 里同名预设整体覆盖内置；新名即新增预设（F2 循环自动纳入）。
  无 YAML 时行为与硬编码版完全一致。

过滤顺序：preset 过滤先执行（allowlist → hidden），再执行 mode 的权限过滤
（plan 剔除 permission>=2）——plan 的只读契约绝对优先。

本模块不得 import core 包内其他模块（tool_registry 引用它）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Preset:
    """预设定义。"""
    name: str                              # standard / minimal / code
    label: str                             # CLI 显示名（中文）
    allowlist: set[str] | None = None      # None = 全部工具；否则只暴露这些工具名
    hidden: set[str] = field(default_factory=set)   # 始终隐藏的工具（如 standard 下隐藏 run_code）
    skip_confirm: bool = False             # 跳过权限确认闸门（策略 deny 永不豁免）
    skip_trigram: bool = False             # 跳过三爻风险否决（防御性——minimal 4 工具不会触发）
    instruction: str = ""                  # 注入 system prompt 的说明（每次调用注入一次）


MINIMAL_INSTRUCTION = (
    "[极简模式] 你只能使用 4 个工具: shell_exec(持久 shell, cwd/环境变量跨调用保持)、"
    "edit_file(行级精确替换)、read_file、list_dir。"
    "工具执行无需用户确认，但策略引擎仍会拦截危险命令。"
    "修改文件优先用 edit_file 精确替换，避免整文件重写。"
)

PTC_INSTRUCTION = (
    "[代码模式 PTC] 你可以编写 Python 程序组合多个工具调用，一次执行：\n"
    '  r = tools.run("read_file", {"path": "a.py"})   # 同步调用工具，r 为字符串结果\n'
    '  tools.run("edit_file", {"path": "a.py", "old_string": "x", "new_string": "y"})\n'
    "  submit(最终结果)   # 调用后程序立即结束，该值作为 run_code 的唯一返回\n"
    "不调用 submit 时，程序 stdout 的最后 2000 字符作为返回。\n"
    "限制: 默认 300 秒墙钟超时、输出累计上限 64KB、单次工具结果上限 8KB。\n"
    "程序内工具调用会经过策略引擎检查，被拒绝时 tools.run 抛 RuntimeError。\n"
    "提交值请保持精简(≤8KB)，过长会被截断。"
)

PRESETS: dict[str, Preset] = {
    "standard": Preset(
        name="standard", label="标准",
        hidden={"run_code"},
    ),
    "minimal": Preset(
        name="minimal", label="极简",
        allowlist={"shell_exec", "edit_file", "read_file", "list_dir"},
        skip_confirm=True, skip_trigram=True,
        instruction=MINIMAL_INSTRUCTION,
    ),
    "code": Preset(
        name="code", label="代码(PTC)",
        instruction=PTC_INSTRUCTION,
    ),
}

PRESET_ORDER = ["standard", "minimal", "code"]


# ── YAML 配置化 ─────────────────────────────────────────────────────────────

def _preset_from_dict(name: str, d: dict) -> Preset:
    """YAML dict → Preset。同名覆盖是整体语义——未写字段取空/False
    （不是回退内置值），覆盖就是覆盖。"""
    allow = d.get("allowlist")
    return Preset(
        name=name,
        label=d.get("label", name),
        allowlist=set(allow) if allow is not None else None,
        hidden=set(d.get("hidden", [])),
        skip_confirm=bool(d.get("skip_confirm", False)),
        skip_trigram=bool(d.get("skip_trigram", False)),
        instruction=d.get("instruction", ""),
    )


def load_presets(
    config_path: str | Path = "config/presets.yaml",
    user_path: str | Path | None = None,
) -> tuple[dict[str, Preset], list[str]]:
    """两层加载：项目级 config/presets.yaml + 用户级 ~/.tianshu/presets.yaml。

    同名预设整体覆盖内置；新名追加。返回 (presets, order)。
    文件不存在/解析失败 → 内置默认（绝不因配置问题挡启动）。
    """
    import yaml

    presets: dict[str, Preset] = dict(PRESETS)
    order: list[str] = list(PRESET_ORDER)

    if user_path is None:
        user_path = Path.home() / ".tianshu" / "presets.yaml"

    for path in (Path(config_path), Path(user_path)):
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            continue  # 坏文件 → 跳过该层，用已合并结果
        for name, d in (data.get("presets") or {}).items():
            if not isinstance(d, dict):
                continue
            presets[name] = _preset_from_dict(name, d)
            if name not in order:
                order.append(name)

    return presets, order


# 已加载的配置化预设（首次访问时惰性加载；tests/热路径共用）
_custom: tuple[dict[str, Preset], list[str]] | None = None


def _get_custom() -> tuple[dict[str, Preset], list[str]]:
    global _custom
    if _custom is None:
        _custom = load_presets()
    return _custom


def reload_presets() -> None:
    """清缓存重载（改配置文件后调用；tests 用）。"""
    global _custom
    _custom = None


def get_preset(name: str) -> Preset:
    """取预设定义；未知名回退 standard（服务器/其他网关始终 standard）。"""
    presets, _ = _get_custom()
    return presets.get(name, presets["standard"])


def cycle_preset(current: str) -> str:
    """standard → minimal → code → (自定义追加项) → standard 循环。"""
    _, order = _get_custom()
    if current not in order:
        return order[0]
    return order[(order.index(current) + 1) % len(order)]


def preset_order() -> list[str]:
    """当前生效的预设循环序（含 YAML 追加项）。"""
    return list(_get_custom()[1])
