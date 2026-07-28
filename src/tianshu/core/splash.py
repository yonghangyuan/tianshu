"""天枢 Agent 启动页 — 中国传统风格 · 云纹 · 北斗七星。

生成带动态信息的 ANSI 彩色启动画面。自动检测终端能力降级。
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# 编码修复
# ═══════════════════════════════════════════════════════════════════════════

def _fix_encoding() -> None:
    """强制 stderr/stdout 使用 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# ANSI 配色 — 中国传统色系
# ═══════════════════════════════════════════════════════════════════════════

class TC:
    RST  = "\033[0m"
    BLD  = "\033[1m"
    DIM  = "\033[2m"
    GOLD   = "\033[33m"
    RED    = "\033[31m"
    AZURE  = "\033[36m"
    WHITE  = "\033[37m"
    TITLE   = BLD + "\033[33m"
    STAR    = BLD + "\033[31m"
    CLOUD   = DIM + "\033[36m"
    LABEL   = BLD + "\033[36m"
    VALUE   = "\033[37m"
    TIP     = DIM + "\033[37m"

    @classmethod
    def enabled(cls) -> bool:
        return sys.stderr.isatty()


# ═══════════════════════════════════════════════════════════════════════════
# 天枢 大字 — 标准 ASCII 艺术字（跨平台兼容）
# ═══════════════════════════════════════════════════════════════════════════

TIANSHU_LOGO = r"""
  ████████╗ ██╗ █████╗ ███╗  ██╗  ███████╗██╗  ██╗██╗  ██╗
  ╚══██╔══╝██╔╝██╔══██╗████╗ ██║  ██╔════╝██║  ██║██║  ██║
     ██║  ██║ ███████║██╔██╗██║  ███████╗███████║██║  ██║
     ██║  ██║ ██╔══██║██║╚████║  ╚════██║██╔══██║██║  ██║
     ██║  ██║ ██║  ██║██║ ╚███║  ███████║██║  ██║╚██████╔╝
     ╚═╝  ╚═╝ ╚═╝  ╚═╝╚═╝  ╚══╝  ╚══════╝╚═╝  ╚═╝ ╚═════╝
"""


# ═══════════════════════════════════════════════════════════════════════════
# 云纹边框 — 两层（宽兼容 + 全Unicode）
# ═══════════════════════════════════════════════════════════════════════════

def _cloud_top(width: int, full: bool = True) -> str:
    """顶部云纹装饰线。"""
    if full:
        cloud = "~*~  ☁  祥 云  ·  瑞 霭  ·  天 枢  ☁  ~*~"
    else:
        cloud = "~*~  ☁  T i a n s h u  ☁  ~*~"
    pad = max(0, (width - len(cloud)) // 2)
    return " " * pad + cloud


def _cloud_bot(width: int, full: bool = True) -> str:
    """底部云纹装饰线。"""
    if full:
        cloud = "~*~  ☁  北 斗 七 星  ·  枢 纽 定 乾 坤  ☁  ~*~"
    else:
        cloud = "~*~  ☁  Big Dipper  ☁  ~*~"
    pad = max(0, (width - len(cloud)) // 2)
    return " " * pad + cloud


# ═══════════════════════════════════════════════════════════════════════════
# 信息面板
# ═══════════════════════════════════════════════════════════════════════════

def _info_panel(
    width: int,
    models: list[dict[str, Any]] | None,
    tool_count: int,
    skill_count: int,
    db_path: str,
) -> list[str]:
    """生成信息面板行。"""
    color = TC.enabled()

    def c(code: str, text: str) -> str:
        return f"{code}{text}{TC.RST}" if color else text

    lines: list[str] = []
    indent = "  "

    # ── 标题行 ──
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(
        indent
        + c(TC.TITLE, "天枢 Agent")
        + c(TC.DIM, "  v0.1.0  ·  ")
        + c(TC.VALUE, now)
    )

    # ── 模型行 ──
    if models:
        model_strs = []
        for m in models[:6]:
            name = m.get("name", "?")
            tags = m.get("tags", set())
            icon = "[R]" if "reasoning" in tags else "[C]" if "fast" in tags else "   "
            model_strs.append(f"{icon} {name}")
        lines.append(indent + c(TC.LABEL, "模型  ") + "  ".join(model_strs))
    else:
        lines.append(indent + c(TC.LABEL, "模型  ") + c(TC.DIM, "未配置 — 输入 --setup 开始"))

    # ── 能力行 ──
    lines.append(
        indent
        + c(TC.LABEL, "能力  ")
        + c(TC.VALUE, str(tool_count)) + c(TC.DIM, " 工具  ·  ")
        + c(TC.VALUE, str(skill_count)) + c(TC.DIM, " Skills  ·  ")
        + c(TC.DIM, "审计: ")
        + c(TC.VALUE, db_path or "内存")
    )

    # ── 架构行 ──
    lines.append(
        indent
        + c(TC.LABEL, "架构  ")
        + c(TC.RED, "☰ 天爻") + c(TC.DIM, "·规律  ")
        + c(TC.GOLD, "☷ 人爻") + c(TC.DIM, "·目的  ")
        + c(TC.AZURE, "☷ 地爻") + c(TC.DIM, "·物质")
    )

    # ── 命令提示 ──
    tips = [
        ("--models", "模型"),
        ("--audit",  "审计"),
        ("--setup",  "配置"),
        ("你好",     "对话"),
    ]
    tip_parts = [c(TC.VALUE, k) + c(TC.DIM, " " + v) for k, v in tips]
    lines.append(indent + c(TC.DIM, "命令  ") + "  │  ".join(tip_parts))

    # ── 分隔线 ──
    lines.append(indent + c(TC.DIM, "─" * min(width - 2, 78)))

    return lines


# ═══════════════════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════════════════

def render(
    to_stderr: bool = True,
    models: list[dict[str, Any]] | None = None,
    tool_count: int = 0,
    skill_count: int = 0,
    db_path: str = "",
) -> str:
    """生成并输出启动页。

    自动检测终端能力：支持 UTF-8 → 全中文版，否则 → ASCII 简化版。
    """
    _fix_encoding()
    color = TC.enabled()

    def c(code: str, text: str) -> str:
        return f"{code}{text}{TC.RST}" if color else text

    width = min(shutil.get_terminal_size().columns, 100)
    full = _supports_chinese()

    lines: list[str] = []
    lines.append("")

    # ── 云纹 ──
    lines.append(c(TC.CLOUD, _cloud_top(width, full)))

    # ── Logo ──
    for logo_line in TIANSHU_LOGO.strip("\n").split("\n"):
        lines.append(c(TC.TITLE, logo_line))

    lines.append("")

    # ── 副标题 ──
    if full:
        subtitle = "北斗七星第一星  ·  主司枢纽与导向  ·  中国本土自主 AI Agent 框架"
    else:
        subtitle = "Polaris of the Big Dipper  ·  Sovereign AI Agent for China"
    pad = max(0, (width - len(subtitle)) // 2)
    lines.append(" " * pad + c(TC.CLOUD, subtitle))

    lines.append("")

    # ── 信息面板 ──
    for il in _info_panel(width, models, tool_count, skill_count, db_path):
        lines.append(il)

    # ── 底部云纹 ──
    lines.append("")
    lines.append(c(TC.CLOUD, _cloud_bot(width, full)))
    lines.append("")

    output = "\n".join(lines)
    if to_stderr:
        sys.stderr.write(output + "\n")
        sys.stderr.flush()
    return output


def _supports_chinese() -> bool:
    """检测终端是否支持中文字符。"""
    term = sys.stderr.encoding or ""
    return "utf" in term.lower() or term == ""


# ═══════════════════════════════════════════════════════════════════════════
# 纯 ASCII 备用版（管道/重定向时自动使用）
# ═══════════════════════════════════════════════════════════════════════════

def render_plain(models_count: int = 0) -> str:
    """纯文本启动页。"""
    return "\n".join([
        "",
        "  +----------------------------------------------+",
        "  |          T I A N S H U   A G E N T           |",
        "  |  北斗七星第一星 · 中国本土自主 AI 框架        |",
        "  +----------------------------------------------+",
        "",
        f"  v0.1.0  |  {models_count} 个模型已加载",
        f"  --models 模型  --audit 审计  --setup 配置Key",
        "",
    ])
