"""Agent 预设 — standard / minimal / code(PTC) 三维度配置。

与 normal/auto/plan 会话模式正交：
  - 会话模式管「确认策略」（normal 逐次确认 / auto 免确认 / plan 只读）
  - 预设管「工具集 + 循环行为」（standard 全量 / minimal 四工具 / code 加 run_code 编程入口）

过滤顺序：preset 过滤先执行（allowlist → hidden），再执行 mode 的权限过滤
（plan 剔除 permission>=2）——plan 的只读契约绝对优先。

本模块不得 import core 包内其他模块（tool_registry 引用它）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


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


def get_preset(name: str) -> Preset:
    """取预设定义；未知名回退 standard（服务器/其他网关始终 standard）。"""
    return PRESETS.get(name, PRESETS["standard"])


def cycle_preset(current: str) -> str:
    """standard → minimal → code → standard 循环。"""
    if current not in PRESET_ORDER:
        return PRESET_ORDER[0]
    return PRESET_ORDER[(PRESET_ORDER.index(current) + 1) % len(PRESET_ORDER)]
