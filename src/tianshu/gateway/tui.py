"""天枢 TUI — Rich Console 终端界面。

简洁可靠：Rich Panel 做边界 + 标准 input()。
无 Textual/Live 依赖——100% 不会跟 input() 冲突。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parents[3]  # gateway → tianshu → src → root
sys.path.insert(0, str(_project_root / "src"))

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

from tianshu.sdk.models import AgentRequest, AgentContext
from tianshu.core.service import AgentCore
from tianshu.core.config import load_providers, load_routing_config
from tianshu.core.setup import load_user_keys, load_default_model

console = Console()
MODE = "NORMAL"
SHOW_THINK = False

# ── Helpers ──────────────────────────────────────────────────────────────

def _mode_bar(model: str, stats: str = "") -> str:
    c = {"NORMAL": "dim", "PLAN": "bold cyan", "AUTO": "bold green"}.get(MODE, "")
    parts = [f"[{c}]◉ {MODE}[/{c}]"]
    if model:
        parts.append(f"[cyan]{model}[/cyan]")
    if stats:
        parts.append(f"[dim]{stats}[/dim]")
    return " │ ".join(parts)

def _prompt() -> Text:
    """输入提示符——显示模式和模型。"""
    return Text.from_markup(f"{_mode_bar(_model)} [dim]▸[/dim] ")

def _splash(core) -> None:
    """启动面板。"""
    table = Table(box=None, padding=(0, 4), show_header=False)
    table.add_column(style="bold cyan")
    table.add_column(style="dim")
    table.add_row("Skills", str(core.skills.count))
    table.add_row("Models", str(core.model_count))
    table.add_row("Plugins", str(core.plugins.count))
    table.add_row("Mode", f"[bold cyan]{MODE}[/bold cyan]  (Ctrl+P Plan, Ctrl+A Auto, Ctrl+N Normal)")
    table.add_row("Help", "model <name> | models | skills | audit | memory | think | exit")
    panel = Panel(table, title="TianShu v0.3.0", title_align="left",
                  border_style="cyan", padding=(1, 2))
    console.print(panel)

# ── Globals (set by main) ────────────────────────────────────────────────

_core: AgentCore | None = None
_ctx: AgentContext | None = None
_model: str = "deepseek/v4-pro"


# ── Chat ─────────────────────────────────────────────────────────────────

async def _chat(text: str) -> None:
    global _core, _ctx, _model
    if _core is None:
        return

    console.print(Text.from_markup(f"[bold #60a5fa]▸[/bold #60a5fa] {text}"))
    console.print(Text.from_markup(f"[dim]  ...[/dim]"), end="\r")

    t0 = time.time()
    task_type = "plan" if MODE == "PLAN" else "conversation"
    req = AgentRequest(input=text, model_override=_model, task_type=task_type)

    try:
        resp = await _core.run(req, ctx=_ctx)
    except Exception as e:
        console.print(Text.from_markup(f"[bold red]  ⚠[/bold red] {e}"))
        return

    elapsed = time.time() - t0
    console.print(" " * 30, end="\r")  # clear "..."

    if resp.error:
        console.print(Text.from_markup(f"[bold red]  ⚠[/bold red] {resp.error}"))
        return

    # Tools
    for tc in resp.tool_calls:
        icon = "[#4ade80]●[/#4ade80]" if tc.get("success") else "[red]✗[/red]"
        console.print(Text.from_markup(f"  {icon} {tc.get('name','?')}"))

    # Thinking
    if _core.last_reasoning:
        rc = _core.last_reasoning
        if SHOW_THINK:
            console.print(Text.from_markup(f"[#64748b]  ··· thinking ({len(rc)} chars)[/#64748b]"))
            for line in rc[:2000].split("\n")[:15]:
                console.print(Text.from_markup(f"[#64748b]  {line}[/#64748b]"))
        else:
            console.print(Text.from_markup(f"[dim]  (thinking: {len(rc)} chars, Ctrl+T)[/dim]"))

    # Content
    if resp.content:
        console.print(resp.content)

    # Stats
    tok = f"{elapsed:.1f}s"
    if resp.prompt_tokens:
        tok += f"  {resp.prompt_tokens//1000}K↑{resp.completion_tokens//1000}K↓"
    console.print(Text.from_markup(f"[dim]  {tok}[/dim]"))
    console.print()


async def _meta(cmd: str) -> None:
    if _core is None:
        return
    if cmd == "models":
        for p in _core._registry.list_all():
            console.print(Text.from_markup(f"[dim]  {p.provider_name}/{p.model_id}[/dim]"))
    elif cmd == "skills":
        for s in _core.skills.list_skills():
            console.print(Text.from_markup(f"[dim]  {s['name']}: {s['description'][:50]}[/dim]"))
    elif cmd == "audit":
        for r in (await _core.audit.recent(3)):
            console.print(Text.from_markup(f"[dim]  {r.get('decision_id','?')} L{r.get('level',1)}[/dim]"))
    elif cmd == "memory":
        c = await _core.memory.count()
        console.print(Text.from_markup(f"[dim]  {c} memories[/dim]"))
    elif cmd == "think":
        rc = _core.last_reasoning
        if rc:
            console.print(Panel(rc[:3000], title="Thinking", border_style="dim"))
        else:
            console.print("[dim](none)[/dim]")
    elif cmd == "plugin":
        for p in _core.plugins.list_plugins():
            console.print(Text.from_markup(f"[dim]  {p['name']}[/dim]"))
    elif cmd == "cron list":
        for j in _core.cron.list_jobs():
            console.print(Text.from_markup(f"[dim]  {j['cron']} -> {j['task_type']}[/dim]"))
    console.print()


# ── Key watcher (background thread for Ctrl+P/A/N/T) ─────────────────────

def _key_watcher() -> None:
    """监听全局热键（后台线程）。"""
    global MODE, SHOW_THINK
    if sys.platform != "win32":
        return  # Unix needs tty setup, skip for now
    try:
        import msvcrt
        while True:
            if not msvcrt.kbhit():
                time.sleep(0.1)
                continue
            ch = msvcrt.getch()
            if ch == b"\x10":  # Ctrl+P
                MODE = "PLAN"
                console.print(Text.from_markup("\r[bold cyan]◉ PLAN[/bold cyan]       \n"))
            elif ch == b"\x01":  # Ctrl+A
                MODE = "AUTO"
                console.print(Text.from_markup("\r[bold green]◉ AUTO[/bold green]       \n"))
            elif ch == b"\x0e":  # Ctrl+N
                MODE = "NORMAL"
                console.print(Text.from_markup("\r[dim]◉ NORMAL[/dim]       \n"))
            elif ch == b"\x14":  # Ctrl+T
                SHOW_THINK = not SHOW_THINK
                console.print(Text.from_markup(f"\r[dim]Thinking: {'ON' if SHOW_THINK else 'OFF'}[/dim]      \n"))
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    global _core, _ctx, _model

    config_dir = _project_root / "config"
    p_yaml = config_dir / "providers.yaml"
    soul_md = config_dir / "soul.md"

    if not p_yaml.exists():
        console.print("[red]config/providers.yaml not found[/red]")
        return

    user_keys = load_user_keys()
    registry = load_providers(p_yaml, extra_keys=user_keys)
    routing = load_routing_config(p_yaml)
    sp = soul_md.read_text(encoding="utf-8") if soul_md.exists() else ""

    _core = AgentCore()
    _core.setup(registry=registry, routing=routing, system_prompt=sp,
                db_path=str(_project_root / "tianshu.db"), skill_discover=True)
    _model = load_default_model() or "deepseek/v4-pro"
    _ctx = AgentContext()
    _ctx.metadata["model_override"] = _model

    _splash(_core)
    console.print(Text.from_markup(f"[dim]{_mode_bar(_model)}[/dim]"))

    # Start hotkey watcher
    import threading
    t = threading.Thread(target=_key_watcher, daemon=True)
    t.start()

    # Main loop
    while True:
        try:
            prompt = _prompt()
            user_input = console.input(prompt)
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见[/dim]")
            break

        text = user_input.strip()
        if not text:
            continue

        if text == "exit" or text == "quit":
            break
        elif text == "help":
            console.print("[dim]model <name> | models | skills | audit | memory | think | exit[/dim]\n")
            continue
        elif text.startswith("model "):
            _model = text[6:].strip()
            _ctx.metadata["model_override"] = _model
            console.print(Text.from_markup(f"[cyan]Model -> {_model}[/cyan]\n"))
            continue
        elif text in ("models", "skills", "audit", "memory", "think", "plugin", "cron list"):
            asyncio.run(_meta(text))
            continue

        asyncio.run(_chat(text))

    console.print("[dim]TianShu closed.[/dim]")


if __name__ == "__main__":
    main()
