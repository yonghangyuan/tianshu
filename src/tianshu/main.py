"""天枢 Agent CLI 入口。

用法：
  python -m src.main                    # 交互模式
  python -m src.main "搜索最新论文"      # 单次执行
  python -m src.main --audit            # 查看最近审计记录
  python -m src.main --models           # 查看模型状态
"""

from __future__ import annotations

import asyncio
import os
import sys
sys.dont_write_bytecode = True  # 防止 .pyc 缓存导致旧代码残留
from pathlib import Path

# 确保项目根目录在 sys.path
_project_root = Path(__file__).resolve().parents[2]  # src/tianshu/ → root

# 模块级导入 — 供所有函数使用
from tianshu.core.config import load_providers, load_routing_config
from tianshu.core.setup import (
    load_default_model, save_default_model, load_user_keys, save_user_keys,
    check_keys, any_key_configured, run_setup_wizard,
)
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Rich 渲染——Phase 1
from tianshu.core import status as _status
from rich.console import Console as _RichConsole
_rich_console = _RichConsole(highlight=False, stderr=True)
_status.set_rich_mode(_rich_console)

# Windows Git Bash / MSYS2 编码修复
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _print_cmd_help() -> None:
    """精简帮助——只显示最常用的命令。"""
    from rich.table import Table as _Table
    from rich import box as _box

    _rich_console.print()
    t = _Table(box=_box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column(style="bold cyan", width=18)
    t.add_column(style="dim")

    shortcuts = [
        ("/help, /h", "帮助"),
        ("/models", "切换模型"),
        ("/setup", "配置 API Key"),
        ("/audit", "审计记录"),
        ("/orchestrate", "多 Agent 编排"),
        ("/plan", "生成计划(不执行)"),
        ("/agent", "管理子 Agent"),
        ("/learn", "生成新技能"),
        ("/session", "会话管理"),
        ("/memory", "记忆管理"),
        ("/rag", "RAG 知识库"),
        ("/star", "星群通信"),
        ("/tools", "查看工具"),
        ("/mode, Shift+Tab", "切换 normal/auto/plan"),
        ("/preset, F2", "切换 标准/极简/代码(PTC)"),
        ("/cost", "Token 消耗"),
        ("/clear", "清屏"),
        ("exit, q", "退出"),
    ]
    for key, desc in shortcuts:
        t.add_row(key, desc)
    _rich_console.print(t)
    _rich_console.print("  [dim]输入 /help all 查看完整命令列表[/dim]")
    _rich_console.print()


def _print_full_help() -> None:
    """完整命令列表。"""
    from rich.table import Table as _Table
    from rich import box as _box
    from tianshu.core.commands import get_registry

    reg = get_registry()
    t = _Table(box=_box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column(style="bold cyan", width=16)
    t.add_column(style="dim")
    for cmd in reg.list_all():
        aliases = "  ".join(
            c for c in cmd.aliases
            if c.startswith("/") and c != f"/{cmd.name}"
        )
        name_col = f"/{cmd.name}"
        if aliases:
            name_col += f"  {aliases}"
        t.add_row(name_col, cmd.description)
    _rich_console.print()
    _rich_console.print(t)
    _rich_console.print()


# ── 更新后的辅助函数（Rich 版本）──

def _show_plugins(pm) -> None:
    """显示 Plugin 状态。"""
    plugins = pm.list_plugins()
    if not plugins:
        _rich_console.print("\n  [dim]暂无 Plugin。[/dim]")
        _rich_console.print("  [dim]将 plugin.py 放入 ~/.tianshu/plugins/<name>/ 即可加载。[/dim]\n")
        return
    _rich_console.print(f"\n  ==== Plugins ([bold]{pm.count}[/bold]) ====")
    for p in plugins:
        hooks = ", ".join(p["hooks"])
        _rich_console.print(f"  🔌 {p['name']}  hooks: [dim]{hooks}[/dim]")
    _rich_console.print()


def _handle_cron(user_input: str, cron) -> None:
    """处理 cron 命令。"""
    parts = user_input.split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else "list"
    if sub == "list":
        jobs = cron.list_jobs()
        if not jobs:
            _rich_console.print("\n  [dim]暂无定时任务。[/dim]")
            _rich_console.print('  [dim]用法: cron add "0 9 * * *" task_type "任务描述"[/dim]\n')
        else:
            _rich_console.print(f"\n  ==== Cron Jobs ([bold]{len(jobs)}[/bold]) ====")
            for j in jobs:
                _rich_console.print(f"  ⏰ {j['cron']} → [{j['task_type']}] {j['input'][:50]}")
            _rich_console.print()
    elif sub == "add" and len(parts) > 2:
        args = parts[2].split(maxsplit=2)
        if len(args) >= 3:
            cron.add(args[0], args[1], args[2])
            _rich_console.print(f"  ✅ 已添加: {args[0]}\n")
    elif sub == "remove" and len(parts) > 2:
        ok = cron.remove(parts[2])
        _rich_console.print(f"  {'✅ 已移除' if ok else '❌ 未找到'}: {parts[2]}\n")


async def _show_memory(mem) -> None:
    """显示记忆状态。"""
    count = await mem.count()
    recent = await mem.list_recent(5)
    _rich_console.print(f"\n  ==== Memory ([bold]{count}[/bold] 条) ====")
    if recent:
        for r in recent:
            _rich_console.print(f"  [[dim]{r['category']}[/dim]] {r['key']}: {r['value'][:60]}")
    else:
        _rich_console.print("  [dim]暂无记忆[/dim]")
    _rich_console.print()


async def _show_audit(store) -> None:
    """打印最近的审计记录。"""
    records = await store.list_recent(5)
    if not records:
        _rich_console.print("[dim]暂无审计记录[/dim]")
        return

    total = await store.count()
    _rich_console.print(f"\n===== 最近 {len(records)} 条审计记录（共 {total} 条）=====")
    for r in records:
        eval_str = ""
        if r.evaluation:
            eval_str = f" | 评估: {r.evaluation.actual_outcome[:60]}"
        _rich_console.print(
            f"  [[cyan]{r.decision_id}[/cyan]] L{r.level} | {r.llm_model} | "
            f"输入={len(r.input_data)}项 | 推理={len(r.reasoning_chain)}步"
            f"{eval_str}"
        )
    _rich_console.print()


def _show_skills(loader, observer) -> None:
    """显示 Skills 状态。"""
    skills = loader.list_skills()
    if not skills:
        _rich_console.print("[dim]  暂无已加载的 Skills[/dim]")
        return

    _rich_console.print(f"\n  ==== Skills ([bold]{loader.skill_count}[/bold] 个) ====")
    _rich_console.print(f"  [dim]内置: {loader.builtin_count} | 用户/自进化: {loader.user_count}[/dim]")
    _rich_console.print(f"  [dim]观测序列: {observer.observation_count}[/dim]\n")
    for s in skills:
        icon = "📦" if not s["is_user"] else "🧬"
        _rich_console.print(f"  {icon} [bold]{s['name']}[/bold]")
        _rich_console.print(f"     [dim]{s['description'][:60]}[/dim]")
        _rich_console.print(f"     [dim]三爻: {s['trigram']} | 工具: {', '.join(s['tools'][:5]) or 'LLM自主'} | 使用: {s['usage']}次[/dim]")
    _rich_console.print()


def _handle_model_command(
    user_input: str,
    registry,  # ProviderRegistry
    ctx,        # AgentContext
    input_handler=None,  # 全屏时挂起执行菜单
) -> None:
    """处理 model 命令。"""
    parts = user_input.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    if arg == "list" or arg == "ls":
        _show_models(registry)
        return

    if arg == "":
        # 无参数 → 弹出菜单选择
        from tianshu.core.menu import select_menu
        models = registry.list_all()
        options = [
            {
                "label": f"{p.provider_name}/{p.model_id}",
                "desc": ", ".join(sorted(p.capabilities)) if p.capabilities else "通用",
            }
            for p in models
        ]

        try:
            idx = select_menu("选择默认模型", options)
        except KeyboardInterrupt:
            _rich_console.print("  [dim]已取消[/dim]")
            return
        if idx is not None:
            chosen = models[idx]
            full = f"{chosen.provider_name}/{chosen.model_id}"
            ctx.metadata["model_override"] = full
            save_default_model(full)
            _rich_console.print(f"  ✅ 已切换默认模型: [cyan]{full}[/cyan]（重启后仍生效）\n")
        return

    # 解析用户输入
    if "/" in arg:
        provider_name, model_short = arg.split("/", 1)
    else:
        provider_name = ""
        model_short = arg
        for p in registry.list_all():
            model_id = p.model_id
            if arg in model_id:
                provider_name = p.provider_name
                model_short = model_id
                break
        if not provider_name:
            _rich_console.print(f"  [red]未找到模型: {arg}[/red]")
            names = ", ".join(f"{p.provider_name}/{p.model_id}" for p in registry.list_all())
            _rich_console.print(f"  [dim]可用: {names}[/dim]")
            return

    full = f"{provider_name}/{model_short.replace(provider_name + '-', '')}"
    ctx.metadata["model_override"] = full
    save_default_model(full)
    _rich_console.print(f"  ✅ 已切换默认模型: [cyan]{full}[/cyan]（重启后仍生效）")


async def _handle_session(
    user_input: str,
    store,  # SessionStore
    ctx: AgentContext,
) -> None:
    """处理 /session 命令。"""
    parts = user_input.split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else "list"

    if sub == "list" or sub == "ls":
        sessions = store.list_sessions(limit=15)
        if not sessions:
            _rich_console.print("\n  [dim]暂无保存的会话[/dim]\n")
            return
        from rich.table import Table as _Table
        from rich import box as _box
        t = _Table(box=_box.SIMPLE, show_header=True, padding=(0, 2))
        t.add_column("ID", style="dim", width=10)
        t.add_column("Title")
        t.add_column("Updated", style="dim")
        for s in sessions:
            updated = time.strftime("%m-%d %H:%M", time.localtime(s["updated_at"]))
            t.add_row(s["id"][:8], s["title"][:60], updated)
        _rich_console.print()
        _rich_console.print(t)
        _rich_console.print(f"  [dim]共 {len(sessions)} 条  |  /session resume <id> 恢复  |  /session delete <id> 删除[/dim]\n")

    elif sub == "resume" and len(parts) > 2:
        session_id = parts[2].strip()
        loaded = store.load(session_id)
        if loaded is None:
            _rich_console.print(f"\n  [red]会话未找到: {session_id[:8]}[/red]\n")
            return
        # 更新当前上下文
        ctx.session_id = loaded.session_id
        ctx.messages = loaded.messages
        ctx.metadata = loaded.metadata
        # 持久 shell 不入库——resume 后丢弃旧句柄（极简模式惰性重建）
        if getattr(ctx, "shell", None) is not None:
            try:
                ctx.shell.stop()
            except Exception:
                pass
        ctx.shell = None
        _rich_console.print(f"\n  ✅ 已恢复会话 [cyan]{session_id[:8]}[/cyan] ({len(ctx.messages)} 条消息)\n")

    elif sub == "delete" and len(parts) > 2:
        session_id = parts[2].strip()
        ok = store.delete(session_id)
        if ok:
            _rich_console.print(f"\n  ✅ 已删除会话 [cyan]{session_id[:8]}[/cyan]\n")
        else:
            _rich_console.print(f"\n  [red]会话未找到: {session_id[:8]}[/red]\n")

    elif sub == "new":
        if getattr(ctx, "shell", None) is not None:
            try:
                ctx.shell.stop()
            except Exception:
                pass
        ctx.session_id = f"sess_{int(time.time())}"
        ctx.messages.clear()
        ctx.metadata.clear()
        ctx.shell = None
        _rich_console.print(f"\n  ✅ 新会话 [cyan]{ctx.session_id[:8]}[/cyan]\n")

    else:
        _rich_console.print("\n  [dim]用法: /session list | resume <id> | new | delete <id>[/dim]\n")


def _show_models(registry) -> None:
    """打印模型状态。"""
    from rich.table import Table as _Table
    from rich import box as _box
    _rich_console.print()
    t = _Table(box=_box.SIMPLE, show_header=True, padding=(0, 2))
    t.add_column("Provider", style="bold cyan")
    t.add_column("Model", style="dim")
    t.add_column("Tags")
    t.add_column("Max Context", style="dim")
    for p in registry.list_all():
        t.add_row(
            p.provider_name,
            p.model_id,
            ", ".join(sorted(p.capabilities)) if p.capabilities else "通用",
            str(p.max_context_tokens),
        )
    _rich_console.print(t)
    _rich_console.print()


def main() -> None:
    """同步主循环 — 每个 LLM 回合独立 asyncio.run()，让 prompt_toolkit 正常工作。"""
    _init_globals()
    _main_sync()


def _main_sync() -> None:
    """初始化 + 同步主循环。"""
    # ── 初始化（同原来 _main 的 setup 部分）──
    from tianshu.core.config import resolve_config_dir
    config_dir = resolve_config_dir(_project_root)
    providers_yaml = config_dir / "providers.yaml"
    soul_md = config_dir / "soul.md"

    if not providers_yaml.exists():
        _rich_console.print(f"[red]❌ {providers_yaml} 未找到[/red]")
        _rich_console.print("[dim]pip 安装用户: mkdir ~/.tianshu/config 并放入 providers.yaml/soul.md，"
                            "或设 TIANSHU_CONFIG_DIR 指向配置目录[/dim]")
        _rich_console.print("[dim]源码用户: 从 config/ 目录复制并填入 API Key[/dim]")
        return

    user_keys = load_user_keys()
    try:
        registry = load_providers(providers_yaml, extra_keys=user_keys)
    except FileNotFoundError as e:
        _rich_console.print(f"[red]❌ 配置文件缺失: {e}[/red]")
        return
    except Exception as e:
        _rich_console.print(f"[red]❌ 加载模型配置失败: {e}[/red]")
        _rich_console.print("[dim]请检查 config/providers.yaml 格式是否正确（YAML 缩进需对齐）[/dim]")
        return

    routing_config = load_routing_config(providers_yaml)
    system_prompt = soul_md.read_text(encoding="utf-8") if soul_md.exists() else ""

    from tianshu.core.service import AgentCore
    core = AgentCore()
    core.setup(
        registry=registry,
        routing=routing_config,
        system_prompt=system_prompt,
        db_path=str(_project_root / "tianshu.db"),
        skill_discover=True,
    )

    from tianshu.gateway.cli import render_splash_rich

    configured = sum(1 for k in check_keys().values() if k)
    model_infos = [
        {"name": f"{p.provider_name}/{p.model_id}", "tags": p.capabilities}
        for p in registry.list_all()
    ]
    render_splash_rich(
        models=model_infos,
        tool_count=len(core.skills.loader.get_all_tools()),
        skill_count=core.skills.count,
        model_count=core.model_count,
        plugin_count=core.plugins.count,
    )
    if configured == 0:
        _rich_console.print("[dim yellow]  ⚠ 未配置 API Key，输入 /setup 开始配置[/dim yellow]")

    # ── 处理命令行参数 ──
    args = sys.argv[1:]
    if args:
        user_input = " ".join(args)
        from tianshu.gateway.cli import chat_once
        if user_input in ("--audit", "/audit", "--models", "/models", "--setup", "/setup"):
            pass  # 略，参数模式用不到
        else:
            asyncio.run(chat_once(core, user_input))
            return

    # ── 上下文 ──
    from tianshu.sdk.models import AgentContext
    ctx = AgentContext()
    default_model = load_default_model()
    if default_model:
        ctx.metadata["model_override"] = default_model

    # ── 输入处理器（同步，prompt_toolkit 可用）──
    from tianshu.core.input import create_input_handler
    from tianshu.gateway.cli import StreamRenderer
    from tianshu.core.commands import get_registry

    cmd_registry = get_registry()
    model_names = [f"{p.provider_name}/{p.model_id}" for p in registry.list_all()]
    # ── 模式系统 ──
    _mode = "normal"

    def _mode_plain(m: str) -> str:
        return {"normal": "⏵", "auto": "⏵⏵", "plan": "◉"}.get(m, "⏵")

    def _cycle_mode():
        nonlocal _mode
        if _mode == "normal":
            _mode = "auto"
            core._automode = True; core._mode = "auto"
        elif _mode == "auto":
            _mode = "plan"
            core._automode = False; core._mode = "plan"
        elif _mode == "plan":
            _mode = "normal"
            core._automode = False; core._mode = "normal"
        # 反馈只在底部状态栏（mode 已入 format_status_bar），不刷屏
        return _mode

    # ── 预设系统（与模式正交：standard / minimal / code + YAML 自定义）──
    _preset = "standard"

    def _preset_plain(p: str) -> str:
        return {"standard": "", "minimal": "◆", "code": "✧"}.get(p, "")

    def _apply_preset(p: str) -> None:
        nonlocal _preset
        if _preset == p:
            return
        # 离开 minimal → 收掉持久 shell
        if _preset == "minimal" and getattr(ctx, "shell", None) is not None:
            try:
                ctx.shell.stop()
            except Exception:
                pass
            ctx.shell = None
        _preset = p
        core._preset = p
        # 反馈只在底部状态栏（preset 已入 format_status_bar），不刷屏

    def _preset_cb(p: str | None = None) -> str:
        """双向：无参=读当前预设；传参=应用（F2 闸门确认后由 handler 调）。"""
        if p is not None:
            _apply_preset(p)
        return _preset

    # ── F4 模型选择（会话级，不落盘；持久默认仍走 /model）──
    def _model_menu_items():
        current = ctx.metadata.get("model_override", "")
        items = []
        for p in registry.list_all():
            tags = sorted(p.capabilities) if p.capabilities else []
            full = f"{p.provider_name}/{p.model_id}"
            items.append({
                "value": full,
                "label": full,
                "desc": "本地" if "local" in tags else ", ".join(tags[:3]),
                "cur": full == current,
            })
        return items

    def _switch_model(full: str) -> None:
        ctx.metadata["model_override"] = full
        # ptk prompt 活跃期间不能直接 print（与 toolbar 渲染打架）——
        # 存消息，主循环轮末统一打印
        nonlocal _pending_notice
        _pending_notice = f"  ⚡ 已切换: {full}（本次会话）"

    renderer = StreamRenderer()

    _pending_notice = ""  # F4 轮内切换反馈，prompt 返回后统一打印

    def _prompt_text():
        m = ctx.metadata.get("model_override", "")
        model_part = m if m else "天枢"
        return f"{_mode_plain(_mode)}{_preset_plain(_preset)} {model_part}> "

    def _status_bar_text():
        """底部状态栏文本——模式/预设/token/缓存命中（会话累计）。

        回合中由 _StatusLine 经 set_status 顶替（spinner 文本）。
        """
        from tianshu.gateway.statusbar import format_status_bar
        try:
            return format_status_bar(
                _mode,
                _preset,
                renderer._total_cost.get("prompt", 0),
                renderer._total_cost.get("completion", 0),
                renderer._total_cost.get("cached", 0),
                renderer._last_cost.get("elapsed", 0.0),
                model=ctx.metadata.get("model_override", ""),
            )
        except Exception:
            return ""

    input_handler = None  # 预绑定：状态栏回调竞态安全
    input_handler = create_input_handler(
        commands=cmd_registry.command_names,
        model_names=model_names,
        mode_callback=_cycle_mode,
        preset_callback=_preset_cb,
        preset_gate=True,  # 敏感预设 F2 闸门（handler 内弹 y/N）
        fullscreen=True,  # inline 状态栏（bottom_toolbar）
        status_callback=_status_bar_text,
        model_menu=_model_menu_items,
        model_callback=_switch_model,
    )

    # ── 会话存储 ──
    from tianshu.memory.session_store import get_session_store
    session_store = get_session_store()

    _print_cmd_help()
    if default_model:
        _rich_console.print(f"  默认模型: [cyan]{default_model}[/cyan]")
    _rich_console.print()

    # ── MCP 状态 ──
    if core._mcp:
        servers = core._mcp.list_servers()
        if servers:
            connected = sum(1 for s in servers if s.get("connected"))
            _rich_console.print(f"  🔌 MCP: {connected}/{len(servers)} server 已连接 ({sum(s['tools'] for s in servers)} 工具)")
        else:
            _rich_console.print(f"  [dim]🔌 MCP: 无已配置的 server[/dim]")
    _rich_console.print()

    # ── 同步主循环 ──
    while True:
        try:
            user_input = input_handler.prompt(_prompt_text()).strip()
        except (EOFError, KeyboardInterrupt):
            _rich_console.print("\n[dim]再见[/dim]")
            break
        finally:
            # F4 轮内切换反馈（prompt 已退出，打印安全）
            if _pending_notice:
                _rich_console.print(_pending_notice)
                _pending_notice = ""

        if not user_input:
            continue

        # ── 命令分发 ──
        if user_input in ("exit", "quit", "q", "/exit"):
            break
        elif user_input in ("audit", "/audit"):
            asyncio.run(_show_audit(core.audit._store))
            continue
        elif user_input in ("models", "/models"):
            _show_models(core._registry)
            continue
        elif user_input in ("setup", "/setup"):
            def _do_setup():
                keys = run_setup_wizard()
                if keys:
                    save_user_keys(keys)
                return keys
            try:
                keys = _do_setup()
            except KeyboardInterrupt:
                _rich_console.print("  [dim]已取消[/dim]")
                continue
            if keys:
                user_keys = load_user_keys()
                registry = load_providers(providers_yaml, extra_keys=user_keys)
                core.setup(registry=registry, routing=routing_config, system_prompt=system_prompt, db_path=str(_project_root / "tianshu.db"), skill_discover=True)
                _rich_console.print(f"\n  ✅ 已保存 {len(keys)} 个 Key。")
            continue
        elif user_input.startswith("model ") or user_input.startswith("/model "):
            _handle_model_command(user_input, core._registry, ctx, input_handler)
            continue
        elif user_input in ("skills", "/skills"):
            _show_skills(core.skills.loader, core.skills.observer)
            continue
        elif user_input.startswith("/loop "):
            _handle_loop(user_input, core)
            continue
        elif user_input in ("project", "/project"):
            _show_project(core)
            continue
        elif user_input.startswith("/project save"):
            _save_project_memory(core)
            continue
        elif user_input in ("status", "/status"):
            _show_status(core, renderer)
            continue
        elif user_input in ("compact", "/compact"):
            _rich_console.print("[dim]正在压缩上下文...[/dim]")
            ctx.messages = ctx.messages[-6:]  # 保留最近3轮
            _rich_console.print(f"[dim]已压缩至 {len(ctx.messages)} 条消息[/dim]")
            _rich_console.print()
            continue
        elif user_input in ("cost", "/cost"):
            _show_cost(renderer)
            continue
        elif user_input in ("help", "/help", "h", "/h"):
            if user_input.endswith(" all") or user_input == "/help all":
                _print_full_help()
            else:
                _print_cmd_help()
            continue
        elif user_input in ("think", "/think"):
            renderer._show_reasoning = not renderer._show_reasoning
            if renderer._show_reasoning:
                _rich_console.print("[dim]💭 思考显示: 开启 (下次回复将展示完整推理)[/dim]")
                # 显示历史推理
                if renderer._reasoning_buf:
                    from rich.panel import Panel as _P
                    full = "".join(renderer._reasoning_buf)
                    _rich_console.print(_P(full[:3000], title="Thinking (历史)", border_style="dim"))
            else:
                _rich_console.print("[dim]💭 思考显示: 折叠 (仅摘要)[/dim]")
            _rich_console.print()
            continue
        elif user_input in ("memory", "/memory"):
            asyncio.run(_show_memory(core.memory))
            continue
        elif user_input.startswith("/memory ") or user_input.startswith("memory "):
            parts = user_input.split(maxsplit=2)
            sub = parts[1] if len(parts) > 1 else ""
            arg = parts[2] if len(parts) > 2 else ""
            if sub == "search" and arg:
                results = asyncio.run(core.memory.recall(arg, limit=10))
                if results:
                    _rich_console.print(f"\n  ==== 搜索 '{arg}' ({len(results)} 条) ====")
                    for r in results:
                        _rich_console.print(f"  [[dim]{r['category']}[/dim]] {r['key']}: {r['value'][:120]}")
                else:
                    _rich_console.print(f"\n  [dim]未找到与 '{arg}' 相关的记忆[/dim]")
                _rich_console.print()
            elif sub == "stats":
                count = asyncio.run(core.memory.count())
                recent = asyncio.run(core.memory.list_recent(10))
                _rich_console.print(f"\n  ==== 记忆统计 ====")
                _rich_console.print(f"  总计: {count} 条")
                if recent:
                    cats = {}
                    for r in recent:
                        cats[r['category']] = cats.get(r['category'], 0) + 1
                    _rich_console.print(f"  分类: {cats}")
                _rich_console.print()
            elif sub == "decay":
                deleted = asyncio.run(core.memory.decay())
                _rich_console.print(f"\n  ✅ 衰减清理: {deleted} 条旧记忆\n")
            else:
                _rich_console.print("\n  [dim]用法: /memory | /memory search <关键词> | /memory stats | /memory decay[/dim]\n")
            continue
        elif user_input in ("plugin", "/plugin"):
            _show_plugins(core.plugins)
            continue
        elif user_input.startswith("cron") or user_input.startswith("/cron"):
            _handle_cron(user_input, core.cron)
            continue
        elif user_input in ("mode", "/mode"):
            _cycle_mode()
            continue
        elif user_input in ("preset", "/preset"):
            _cycle_preset()
            continue
        elif user_input.startswith("session") or user_input.startswith("/session"):
            asyncio.run(_handle_session(user_input, session_store, ctx))
            continue
        elif user_input.startswith("/plan ") or user_input.startswith("plan "):
            core._force_plan = True
            user_input = user_input.split(maxsplit=1)[1] if " " in user_input else user_input
            _rich_console.print(f"[bold cyan]Plan mode forced[/bold cyan]")
            # fall through to conversation
        elif user_input in ("tools", "/tools"):
            _show_tools(core)
            continue
        elif user_input in ("reload", "/reload"):
            _do_reload(core, providers_yaml, routing_config, system_prompt)
            continue
        elif user_input.startswith("/learn ") or user_input.startswith("learn "):
            desc = user_input.split(maxsplit=1)[1] if " " in user_input else ""
            if not desc:
                _rich_console.print("[dim]用法: /learn <技能描述>[/dim]")
                _rich_console.print("[dim]例如: /learn 把刚才搜索论文并生成笔记的操作变成技能[/dim]")
                continue
            _rich_console.print(f"\n[bold cyan]🧬 /learn[/bold cyan] {desc}")
            _rich_console.print("[dim]正在分析最近操作并生成技能...[/dim]")
            _rich_console.print()

            # 构建 prompt
            from tianshu.renyao.skills.learn import build_learn_prompt, parse_skill_md
            # 收集最近使用的工具
            recent_tools: list[str] = []
            if hasattr(core, 'last_reasoning'):
                pass
            # 获取可用工具列表
            all_tools = list(core.skills.loader.get_all_tools()) if core.skills else []
            tool_names = [t.get("function", {}).get("name", "") for t in all_tools if isinstance(t, dict)]

            prompt = build_learn_prompt(
                description=desc,
                recent_tools=recent_tools,
                recent_conversation="",
                available_tools=tool_names,
            )

            # 发送给 LLM
            async def _learn():
                from tianshu.sdk.models import AgentRequest, AgentContext
                ctx = AgentContext()
                return await core.run(
                    AgentRequest(input=prompt, task_type="skill_generation"),
                    ctx,
                )
            try:
                resp = asyncio.run(_learn())
                if resp and resp.content:
                    parsed = parse_skill_md(resp.content)
                    if parsed:
                        meta, body = parsed
                        skill_name = meta.get("name", "unnamed")
                        skill_path = Path.home() / ".tianshu" / "skills" / f"{skill_name}.md"
                        skill_path.parent.mkdir(parents=True, exist_ok=True)

                        # 构建完整 SKILL.md
                        import yaml
                        full_md = f"---\n{yaml.dump(meta, allow_unicode=True, default_flow_style=False)}---\n\n{body}"
                        skill_path.write_text(full_md, encoding="utf-8")

                        _rich_console.print(f"  ✅ 技能已保存: [cyan]{skill_name}[/cyan]")
                        _rich_console.print(f"     路径: {skill_path}")
                        _rich_console.print(f"     {len(meta.get('tools', []))} 个工具 · 关键词: {', '.join(meta.get('trigger_keywords', [])[:5])}")

                        # 重新加载技能
                        if core.skills:
                            core.skills.discover_and_load()
                            core._tool_registry.scan_skills(core.skills.loader)
                            _rich_console.print(f"     技能已加载，现在即可使用。")
                    else:
                        _rich_console.print(f"  [yellow]⚠ LLM 返回格式不符合 SKILL.md，请重试或手动编辑[/yellow]")
                        _rich_console.print(f"  [dim]原始回复: {resp.content[:300]}[/dim]")
                else:
                    _rich_console.print(f"  [red]❌ 生成失败，请检查 API 连接[/red]")
            except Exception as e:
                _rich_console.print(f"  [red]❌ {e}[/red]")
            _rich_console.print()
            continue
        elif user_input.startswith("/agent ") or user_input.startswith("agent "):
            parts = user_input.split(maxsplit=2)
            sub = parts[1] if len(parts) > 1 else "list"
            if sub == "list":
                _rich_console.print(core.orchestrator.status_summary())
            elif sub == "create" and len(parts) > 2:
                args = parts[2].split(maxsplit=2)
                name = args[0]
                skills = args[1].split(",") if len(args) > 1 else ["web_search"]
                model = args[2] if len(args) > 2 else "deepseek-v4-flash"
                agent = asyncio.run(core.orchestrator.create_agent(name, skills, model))
                _rich_console.print(f"  ✅ Agent [cyan]{name}[/cyan] 已创建 ({agent.agent_id})")
            elif sub == "destroy" and len(parts) > 2:
                name = parts[2]
                agent = core.orchestrator.by_name.get(name)
                if agent:
                    asyncio.run(core.orchestrator.destroy(agent))
                    _rich_console.print(f"  ✅ Agent [cyan]{name}[/cyan] 已销毁")
                else:
                    _rich_console.print(f"  [red]Agent '{name}' 未找到[/red]")
            else:
                _rich_console.print("[dim]用法: /agent list | create <name> <skills> | destroy <name>[/dim]")
            continue
        elif user_input in ("clear", "/clear"):
            _rich_console.clear()
            ctx.messages.clear()  # 同时清对话历史
            _rich_console.print("[dim]屏幕+对话历史已清[/dim]")
            continue
        elif user_input.startswith("/orchestrate ") or user_input.startswith("orchestrate "):
            task = user_input.split(maxsplit=1)[1] if " " in user_input else user_input
            _rich_console.print(f"\n[bold cyan]═══ 多 Agent 编排 ═══[/bold cyan]")
            plan = asyncio.run(core.orchestrator.plan(task))
            _rich_console.print(f"[dim]{plan.summary()}[/dim]")
            _rich_console.print()

            # 检测并行任务: "同时" 关键词或多步骤无依赖关系
            can_parallel = (
                "同时" in task or "分别" in task or "各自" in task or
                all(not s.depends_on for s in plan.steps)
            ) and len(plan.steps) >= 2

            if can_parallel:
                _rich_console.print(f"  [dim]→ 检测到可并行任务, 使用并行模式[/dim]")
                tasks = [(s.agent_name, s.task, s.tools_allowed) for s in plan.steps]
                results = asyncio.run(core.orchestrator.execute_parallel(tasks))
                for name, result in results.items():
                    _rich_console.print(f"  [bold green]✓[/bold green] [cyan]{name}[/cyan] → {result[:120]}")
            else:
                total = len(plan.steps)
                for i, step in enumerate(plan.steps):
                    status = f"[{i+1}/{total}]" if total > 1 else ""
                    _rich_console.print(f"  [bold yellow]⏳[/bold yellow] {status} [cyan]{step.agent_name}[/cyan]: {step.task[:60]}...")
                    agent = asyncio.run(core.orchestrator.create_agent(
                        step.agent_name, step.tools_allowed, "deepseek-v4-flash",
                    ))
                    msg = asyncio.run(core.orchestrator.dispatch(agent, step.task, deps=step.depends_on))
                    result = msg.payload.get("result", str(msg.intent)[:200]) if msg.payload else str(msg.intent)
                    _rich_console.print(f"  [bold green]✓[/bold green] {status} [cyan]{step.agent_name}[/cyan] 完成 → {result[:120]}")
                    asyncio.run(core.orchestrator.destroy(agent))
            _rich_console.print(f"\n[bold green]═══ 编排完成 ({len(plan.steps)} 步) ═══[/bold green]\n")
            continue
        elif user_input in ("agents", "/agents"):
            _rich_console.print(core.orchestrator.status_summary())
            continue
        elif user_input.startswith("/plan ") or user_input.startswith("plan "):
            task = user_input.split(maxsplit=1)[1] if " " in user_input else user_input
            if not task:
                _rich_console.print("[dim]用法: /plan <任务描述>[/dim]")
                continue
            plan = asyncio.run(core.orchestrator.plan(task))
            _rich_console.print(plan.summary())
            _rich_console.print("\n[dim]以上为计划，未执行。使用 /orchestrate 执行。[/dim]")
            continue

        # ── MCP 命令 ──
        elif user_input in ("mcp", "/mcp"):
            _show_mcp_status(core)
            continue
        elif user_input.startswith("mcp ") or user_input.startswith("/mcp "):
            parts = user_input.split(maxsplit=2)
            sub = parts[1] if len(parts) > 1 else ""
            arg = parts[2] if len(parts) > 2 else ""
            _handle_mcp_command(sub, arg, core)
            continue

        # ── RAG 命令 ──
        elif user_input in ("rag", "/rag"):
            _handle_rag_command("", "")
            continue
        elif user_input.startswith("rag ") or user_input.startswith("/rag "):
            parts = user_input.split(maxsplit=2)
            sub = parts[1] if len(parts) > 1 else ""
            arg = parts[2] if len(parts) > 2 else ""
            _handle_rag_command(sub, arg)
            continue

        # ── 星群通信命令 ──
        elif user_input in ("star", "/star"):
            _handle_star_command("", "", core)
            continue
        elif user_input.startswith("star ") or user_input.startswith("/star "):
            parts = user_input.split(maxsplit=2)
            sub = parts[1] if len(parts) > 1 else ""
            arg = parts[2] if len(parts) > 2 else ""
            _handle_star_command(sub, arg, core)
            continue

        # ── @file 文件引用解析 ──
        resolved_input = _resolve_at_refs(user_input)

        # ── 对话（每次独立 asyncio.run）──
        from tianshu.sdk.models import (
            AgentRequest, ToolCallConfirm, ToolCallStart, ToolCallResult,
            ContentDelta, StreamError, StreamDone,
        )
        from tianshu.gateway.cli import _handle_confirm, _format_tool_args as _brief_tool_args

        if resolved_input != user_input:
            _rich_console.print("  [dim]已读取 @ 引用的文件[/dim]")
        _rich_console.print()
        renderer.reset()

        from rich.panel import Panel as _Panel
        from rich.status import Status as _Status

        def _flush_thinking(renderer, console):
            """展示折叠的思考面板（如果思考被隐藏且未展示过）。"""
            if not renderer._reasoning_buf or renderer._show_reasoning:
                return
            if getattr(renderer, '_thinking_shown', False):
                return
            renderer._thinking_shown = True
            from rich.panel import Panel as _Pnl2
            from rich.text import Text as _Txt2
            full = "".join(renderer._reasoning_buf)
            first_line = full.split("\n")[0][:120] if full else ""
            if len(full) - len(first_line) > 10:
                console.print(_Pnl2(
                    _Txt2(f"{first_line}...", style="dim italic"),
                    title=f"[dim]Thinking ({len(full)} chars)[/dim]",
                    subtitle="[dim]/think 展开[/dim]",
                    border_style="dim", padding=(0, 1),
                ))
                console.print()

        async def _run_turn():
            import time as _time
            _t0 = _time.time()
            _content_buf: list = []  # 工具执行期间缓冲内容
            _turn_debug(f"turn start: input={resolved_input[:40]!r}")

            with _StatusLine(input_handler, f"[bold #60a5fa]Thinking... (0.0s)") as status:
                _tool_active = False
                _content_started = False
                _timer_task = None

                _spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

                async def _update_timer():
                    i = 0
                    while True:
                        await asyncio.sleep(0.1)
                        elapsed = _time.time() - _t0
                        frame = _spinner_frames[i % len(_spinner_frames)]
                        i += 1
                        status.update(
                            f"{frame} Thinking... ({elapsed:.1f}s)   "
                            "[dim]Ctrl+C 取消[/dim]"
                        )

                _timer_task = asyncio.create_task(_update_timer())

                try:
                    _turn_debug("run_stream start")
                    async for event in core.run_stream(
                        AgentRequest(input=resolved_input, task_type="conversation"),
                        ctx=ctx,
                    ):
                        _turn_debug(f"event: {type(event).__name__}")
                        if isinstance(event, ToolCallConfirm):
                            status.stop()
                            _handle_confirm(event, core)
                            status.start()
                        elif isinstance(event, ToolCallStart):
                            if not _tool_active:
                                status.start()
                            _tool_active = True
                            status.update(f"[bold yellow]⏳ {event.tool_name}[/bold yellow] {_brief_tool_args(event.tool_name, event.tool_args)}")
                        elif isinstance(event, ToolCallResult):
                            icon = "[bold green]✓[/bold green]" if event.success else "[bold red]✗[/bold red]"
                            status.update(f"{icon} {event.tool_name} ({event.elapsed_ms}ms)")
                        elif isinstance(event, StreamError):
                            status.stop()
                            _rich_console.print(_Panel(
                                f"[bold]{event.message}[/bold]",
                                title="[bold red]✗ Error[/bold red]",
                                border_style="red",
                                padding=(1, 2),
                            ))
                        elif isinstance(event, ContentDelta):
                            if _tool_active:
                                _content_buf.append(event)  # 工具执行中→缓存
                            else:
                                if not _content_started:
                                    status.stop()
                                    _content_started = True
                                    # 内容开始输出 → 停掉 Thinking timer，
                                    # 否则它每 0.1s 把状态栏覆盖回 Thinking
                                    if _timer_task:
                                        _timer_task.cancel()
                                        _timer_task = None
                                    _flush_thinking(renderer, _rich_console)
                                renderer.handle(event)  # 工具未激活→直接渲染
                        elif isinstance(event, StreamDone):
                            status.stop()
                            _flush_thinking(renderer, _rich_console)
                            for ev in _content_buf:  # 渲染工具期间缓存的内容
                                renderer.handle(ev)
                            renderer.handle(event)
                        else:
                            renderer.handle(event)
                    _turn_debug("run_stream done")
                finally:
                    if _timer_task:
                        _timer_task.cancel()

        async def _turn_driver():
            """回合驱动：inline 模式下 PromptSession 不在前台，Ctrl+C 走
            原生 KeyboardInterrupt（外层 except 已处理）——无取消竞速。"""
            _turn_debug("turn_driver start (inline)")
            turn_task = asyncio.create_task(_run_turn())
            await turn_task
            _turn_debug("turn_driver end")

        try:
            asyncio.run(_turn_driver())
        except KeyboardInterrupt:
            _rich_console.print("\n  [dim]已取消[/dim]")
        except Exception as e:
            msg = str(e)
            title = "✗ Error"
            if "connect" in msg.lower() or "timeout" in msg.lower() or "refused" in msg.lower():
                title = "🌐 网络不通"
                hint = "ping api.deepseek.com"
            elif "401" in msg or "403" in msg:
                title = "🔑 鉴权失败"
                hint = "/setup 重新配置 API Key"
            elif "429" in msg or "rate" in msg.lower():
                title = "⏳ 请求太频繁"
                hint = "等待 5-10 秒后重试"
            elif "not set up" in msg.lower():
                title = "⚙️ 未初始化"
                hint = "/setup 启动配置向导"
            else:
                title = "✗ 错误"
                hint = "/help 查看可用命令"
            _rich_console.print(_Panel(
                f"[bold]{msg[:300]}[/bold]\n\n[dim]→ 试试: {hint}[/dim]",
                title=f"[bold red]{title}[/bold red]",
                border_style="red",
                padding=(1, 2),
            ))

        # 工具调用永久记录
        if renderer._tool_count > 0:
            from rich.table import Table as _Tbl
            tt = _Tbl(box=None, padding=(0, 2), show_header=False)
            tt.add_column(style="dim", width=4)
            tt.add_column(style="cyan")
            tt.add_column(style="dim", width=15)
            tt.add_row("", "Tools", f"{renderer._tool_count} calls")
            _rich_console.print(tt)

        _rich_console.print()
        session_store.save(ctx, title=user_input[:50])

    # 退出清理：收掉持久 shell（极简模式）
    if getattr(ctx, "shell", None) is not None:
        try:
            ctx.shell.stop()
        except Exception:
            pass
    # 退出全屏 UI：恢复标准流与终端（之后打印到真实终端）
    try:
        input_handler.close()
    except Exception:
        pass
    _rich_console.print("[dim]天枢已关闭[/dim]")


def _show_status(core, renderer) -> None:
    """显示系统状态。"""
    cost = getattr(renderer, '_last_cost', {}) or {}
    _rich_console.print(f"  模型: [cyan]{cost.get('model', '?')}[/cyan]")
    _rich_console.print(f"  模式: [dim]{core._mode}[/dim]")
    _rich_console.print(f"  预设: [dim]{getattr(core, '_preset', 'standard')}[/dim]")
    if cost:
        hit = _cache_hit_str(cost.get('cached', 0), cost.get('prompt', 0))
        _rich_console.print(f"  Token: ↓{cost.get('prompt',0)//1000}K ↑{cost.get('completion',0)//1000}K · ⚡缓存命中 {hit}")
    _rich_console.print(f"  记忆: {asyncio.run(core.memory.count())} 条")
    _rich_console.print(f"  子Agent: {core.orchestrator.active_count} 活跃")
    _rich_console.print(f"  项目: [dim]{getattr(core, '_project_slug', '?')}[/dim]")
    _rich_console.print()


def _handle_loop(user_input: str, core) -> None:
    """解析 /loop 5m 搜索论文 格式。"""
    import re as _re
    parts = user_input.split(maxsplit=1)[1] if " " in user_input else ""
    m = _re.match(r"(\d+)([smhd])\s+(.+)", parts)
    if not m:
        _rich_console.print("[dim]用法: /loop 5m 搜索今日论文[/dim]")
        _rich_console.print("[dim]时间单位: s(秒) m(分) h(时) d(天)[/dim]")
        return
    num, unit, task = int(m.group(1)), m.group(2), m.group(3)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    interval_s = num * multipliers.get(unit, 60)
    cron_expr = f"*/{max(1, interval_s // 60)} * * * *" if interval_s >= 60 else f"* * * * *"
    core.cron.add(f"loop_{hash(task) % 10000}", cron_expr, "conversation", task)
    core.cron.save()
    _rich_console.print(f"  ✅ /loop {num}{unit} → [cyan]{task}[/cyan]")
    _rich_console.print(f"     cron: {cron_expr}  (每 {interval_s}s)\n")


def _show_project(core) -> None:
    """显示当前项目信息。"""
    slug = getattr(core, '_project_slug', '')
    pdir = getattr(core, '_project_dir', None)
    if not pdir:
        _rich_console.print("[dim]未检测到项目上下文[/dim]")
        return
    mem = pdir / "TIANSHU_MEMORY.md"
    _rich_console.print(f"  项目: [cyan]{slug}[/cyan]")
    _rich_console.print(f"  记忆: [dim]{'TIANSHU_MEMORY.md' if mem.exists() else '(无)'}[/dim]")
    _rich_console.print(f"  路径: [dim]{pdir}[/dim]")
    if mem.exists():
        size = len(mem.read_text(encoding="utf-8", errors="replace"))
        _rich_console.print(f"  大小: {size} chars")
    _rich_console.print()


def _save_project_memory(core) -> None:
    """保存项目记忆到 TIANSHU_MEMORY.md。"""
    pdir = getattr(core, '_project_dir', None)
    if not pdir:
        _rich_console.print("[dim]未检测到项目上下文[/dim]")
        return
    mem = pdir / "TIANSHU_MEMORY.md"
    # 收集最近的对话摘要
    provider = core._memory.provider
    recent = asyncio.run(provider.list_recent(20))
    lines = [f"# 项目记忆 · {core._project_slug}\n", f"> 更新时间: {time.strftime('%Y-%m-%d %H:%M')}\n"]
    for r in recent:
        if r['category'] in ('conversation', 'fact', 'delegation'):
            lines.append(f"- [{r['category']}] {r['key']}: {r['value'][:200]}")
    mem.write_text("\n".join(lines), encoding="utf-8")
    _rich_console.print(f"  ✅ 已保存 {len(recent)} 条记忆 → {mem}\n")


def _cache_hit_str(cached: int, prompt: int) -> str:
    """缓存命中率展示：cached/prompt，prompt=0 时显示 –。"""
    if not prompt:
        return "–"
    return f"{cached / prompt * 100:.0f}%"


def _strip_markup(text: str) -> str:
    """剥掉 rich 标记（[bold ...]）——状态栏纯文本渲染用。"""
    import re as _re
    return _re.sub(r"\[/?[^\]]*?\]", "", text)


def _turn_debug(msg: str) -> None:
    """回合诊断日志（TIANSHU_DEBUG=1 时启用）——真机卡死定位用。"""
    import os as _os
    if not _os.environ.get("TIANSHU_DEBUG"):
        return
    try:
        from pathlib import Path as _P
        _log = _P.home() / ".tianshu" / "turn_debug.log"
        _log.parent.mkdir(parents=True, exist_ok=True)
        with open(_log, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {msg}\n")
    except Exception:
        pass


class _StatusLine:
    """回合内状态显示——inline 模式走 rich Status（终端不接管，spinner 可用）。

    有 set_status 的输入处理器（ToolbarHandler）同步把剥掉标记的文本
    存进 status_override，供打字期间 toolbar 显示回合中状态。
    """

    def __init__(self, input_handler, text: str) -> None:
        self._handler = input_handler
        self._text = text
        self._rich = None

    def __enter__(self):
        set_status = getattr(self._handler, "set_status", None)
        if set_status is not None:
            set_status(_strip_markup(self._text))
        self._rich = _rich_console.status(self._text, spinner="dots")
        self._rich.__enter__()
        return self

    def update(self, text: str) -> None:
        set_status = getattr(self._handler, "set_status", None)
        if set_status is not None:
            set_status(_strip_markup(text))
        if self._rich is not None:
            self._rich.update(text)

    def stop(self) -> None:
        set_status = getattr(self._handler, "set_status", None)
        if set_status is not None:
            set_status("")
        if self._rich is not None:
            self._rich.stop()

    def start(self) -> None:
        if self._rich is not None:
            self._rich.start()

    def __exit__(self, *exc) -> bool:
        set_status = getattr(self._handler, "set_status", None)
        if set_status is not None:
            set_status("")
        if self._rich is not None:
            self._rich.__exit__(*exc)
        return False


def _show_cost(renderer) -> None:
    """显示最近一次 + 会话累计 token 消耗。"""
    cost_info = getattr(renderer, '_last_cost', None)
    total = getattr(renderer, '_total_cost', {"prompt": 0, "completion": 0})
    if not cost_info:
        _rich_console.print("[dim]暂无消耗数据[/dim]")
        return
    last_hit = _cache_hit_str(cost_info.get('cached', 0), cost_info.get('prompt', 0))
    _rich_console.print(
        f"  上次: ↓{cost_info.get('prompt',0)//1000}K ↑{cost_info.get('completion',0)//1000}K  "
        f"⚡缓存命中 {last_hit}  {cost_info.get('elapsed',0):.1f}s"
    )
    if total["prompt"] > cost_info.get("prompt", 0):
        total_hit = _cache_hit_str(total.get("cached", 0), total["prompt"])
        _rich_console.print(
            f"  累计: ↓{total['prompt']//1000}K ↑{total['completion']//1000}K  "
            f"⚡缓存命中 {total_hit}  [dim]{cost_info.get('model','')}[/dim]"
        )
    _rich_console.print()


def _show_tools(core) -> None:
    """显示所有已注册的工具。"""
    reg = core._tool_registry
    if not reg:
        _rich_console.print("[dim]ToolRegistry 未初始化[/dim]")
        return
    _rich_console.print()
    _rich_console.print(reg.stats())
    _rich_console.print()


# ── MCP 命令处理器 ─────────────────────────────────────────────────


def _show_mcp_status(core) -> None:
    """显示 MCP server 状态和工具列表。"""
    _rich_console.print()
    if core._mcp is None:
        _rich_console.print("[dim]MCP 未配置。请在 config/mcp.yaml 中添加 server 后 /reload[/dim]")
        _rich_console.print()
        return

    servers = core._mcp.list_servers()
    if not servers:
        _rich_console.print("[dim]没有配置 MCP server。[/dim]")
        _rich_console.print()
        return

    from rich.table import Table as _Table
    t = _Table(title="MCP Servers", box=None)
    t.add_column("Server", style="cyan")
    t.add_column("Transport", style="dim")
    t.add_column("状态")
    t.add_column("工具数", justify="right")

    for s in servers:
        status = "[green]● 已连接[/green]" if s["connected"] else "[red]○ 未连接[/red]"
        t.add_row(s["name"], s["transport"], status, str(s["tools"]))

    _rich_console.print(t)

    # 列出工具
    tools = core._mcp.list_tools()
    if tools:
        _rich_console.print()
        _rich_console.print("[bold]MCP 工具:[/bold]")
        for t in tools:
            _rich_console.print(
                f"  [cyan]{t['name']}[/cyan] "
                f"← [dim]{t['server']}/{t['original_name']}[/dim]"
            )
            if t.get("description"):
                _rich_console.print(f"    [dim]{t['description'][:100]}[/dim]")
    _rich_console.print()


def _handle_mcp_command(sub: str, arg: str, core) -> None:
    """处理 /mcp <sub> [arg] 命令。"""
    if core._mcp is None:
        _rich_console.print("\n[dim]MCP 未配置。[/dim]\n")
        return

    if sub in ("servers", "s", "list"):
        _show_mcp_status(core)

    elif sub in ("tools", "t"):
        tools = core._mcp.list_tools()
        _rich_console.print()
        if not tools:
            _rich_console.print("[dim]没有注册的 MCP 工具[/dim]")
        else:
            for t in tools:
                _rich_console.print(
                    f"  [cyan]{t['name']}[/cyan] "
                    f"← [dim]{t['server']}/{t['original_name']}[/dim]"
                )
        _rich_console.print()

    elif sub in ("reload", "r"):
        from tianshu.core.config import load_mcp_config
        import asyncio as _asyncio

        async def _mcp_reload():
            if core._mcp:
                await core._mcp.disconnect_all()
            config = load_mcp_config("config/mcp.yaml")
            if config.get("servers"):
                await core._mcp.connect_all(
                    config["servers"], core._tool_registry
                )
            return core._mcp.list_servers() if core._mcp else []

        try:
            servers = _asyncio.run(_mcp_reload())
            connected = sum(1 for s in servers if s.get("connected"))
            _rich_console.print(
                f"\n  ✅ MCP 重载完成: {len(servers)} server, "
                f"{connected} 已连接\n"
            )
        except Exception as e:
            _rich_console.print(f"\n  [red]MCP 重载失败: {e}[/red]\n")

    elif sub in ("connect", "c") and arg:
        import asyncio as _asyncio
        config = core._mcp._server_configs.get(arg)
        if not config:
            _rich_console.print(f"\n  [red]未找到 MCP server: {arg}[/red]\n")
            return
        try:
            _asyncio.run(core._mcp.connect_server(arg, config))
            _rich_console.print(f"\n  ✅ 已连接: {arg}\n")
        except Exception as e:
            _rich_console.print(f"\n  [red]连接失败 [{arg}]: {e}[/red]\n")

    elif sub in ("disconnect", "d") and arg:
        import asyncio as _asyncio
        _asyncio.run(core._mcp.disconnect_server(arg))
        _rich_console.print(f"\n  ✅ 已断开: {arg}\n")

    elif sub == "health":
        import asyncio as _asyncio
        status = _asyncio.run(core._mcp.health_check())
        _rich_console.print()
        for name, ok in status.items():
            icon = "[green]●[/green]" if ok else "[red]○[/red]"
            _rich_console.print(f"  {icon} {name}")
        _rich_console.print()

    else:
        _rich_console.print(
            "\n[dim]用法: /mcp [servers|tools|reload|connect <name>|disconnect <name>|health][/dim]\n"
        )


def _handle_rag_command(sub: str, arg: str) -> None:
    """处理 /rag <sub> [arg] 命令。"""
    from tianshu.rag.service import get_service
    svc = get_service()

    if sub in ("status", "s", ""):
        st = asyncio.run(svc.status())
        mode = "[yellow]离线 Mock[/yellow] (未配置 API Key)" if st["offline"] else f"API ([cyan]{st['embedder']}[/cyan])"
        _rich_console.print(f"\n  ==== RAG 知识库 ====")
        _rich_console.print(f"  embedding: {mode}")
        _rich_console.print(f"  存储: [dim]{st['db']}[/dim]")
        cols = st["collections"]
        if not cols:
            _rich_console.print("  [dim]暂无集合 — 用 /rag ingest <路径> 摄取文档[/dim]")
        for c in cols:
            _rich_console.print(f"  📚 [bold]{c['name']}[/bold]: {c['chunks']} chunks / {c['sources']} 来源")
        _rich_console.print()

    elif sub in ("search", "q") and arg:
        results = asyncio.run(svc.search(arg))
        _rich_console.print(f"\n  ==== RAG 检索 '[cyan]{arg}[/cyan]' ({len(results)} 条) ====")
        if not results:
            _rich_console.print("  [dim]未找到相关内容 — 先 /rag ingest <路径> 摄取文档[/dim]")
        for r in results:
            title = r.get("title") or r.get("source") or "片段"
            src = f" [dim]{r.get('source', '')}[/dim]" if r.get("source") else ""
            _rich_console.print(f"  #{r['rank']} [bold]{title}[/bold]{src} (score={r.get('score', 0.0):.3f})")
            _rich_console.print(f"    [dim]{r['text'][:200]}[/dim]")
        _rich_console.print()

    elif sub in ("ingest", "i") and arg:
        try:
            r = asyncio.run(svc.ingest_path(arg))
        except FileNotFoundError as e:
            _rich_console.print(f"\n  [red]{e}[/red]\n")
            return
        _rich_console.print(f"\n  ✅ 摄取: {r['files']} 文件, {r['chunks']} chunks, 新增 {r['added']}")
        if r["skipped"]:
            shown = ", ".join(str(s) for s in r["skipped"][:3])
            _rich_console.print(f"  [dim]跳过 {len(r['skipped'])}: {shown}[/dim]")
        _rich_console.print()

    elif sub in ("delete", "d") and arg:
        n = asyncio.run(svc.delete_collection(arg))
        _rich_console.print(f"\n  ✅ 已删除集合 '[cyan]{arg}[/cyan]' ({n} chunks)\n")

    else:
        _rich_console.print(
            "\n[dim]用法: /rag | /rag search <查询> | /rag ingest <路径> | /rag delete <集合>[/dim]\n"
        )


def _handle_star_command(sub: str, arg: str, core) -> None:
    """处理 /star 命令 — 星群消息总线状态 (P2-006)。"""
    bus = core.orchestrator.bus
    if sub in ("", "status", "s"):
        st = bus.stats()
        _rich_console.print(f"\n  ==== 星群通信总线 ====")
        _rich_console.print(
            f"  总消息: [cyan]{st['total_messages']}[/cyan] | "
            f"记忆板条目: [cyan]{st['board_keys']}[/cyan]"
        )
        for t, subs in st["topics"].items():
            _rich_console.print(f"  📢 话题 [bold]{t}[/bold]: {', '.join(subs) or '(无订阅者)'}")
        inboxes = {a: n for a, n in st["inboxes"].items() if n}
        for a, n in inboxes.items():
            _rich_console.print(f"  📥 [bold]{a}[/bold]: {n} 条未读")
        if not st["topics"] and not inboxes:
            _rich_console.print("  [dim]总线空闲 — 子 Agent 可用 send_message/read_inbox/read_board/post_board 直接通信[/dim]")
        _rich_console.print()
    elif sub in ("inbox", "i") and arg:
        msgs = bus.inbox(arg)
        _rich_console.print(f"\n  ==== {arg} 的收件箱 ({len(msgs)} 条) ====")
        for m in msgs:
            _rich_console.print(f"  [{m.source} → {m.target}] {m.intent}")
        if not msgs:
            _rich_console.print("  [dim]空[/dim]")
        _rich_console.print()
    elif sub in ("board", "b"):
        entries = bus.board_snapshot()
        _rich_console.print(f"\n  ==== 共享记忆板 ({len(entries)} 条) ====")
        for e in entries:
            _rich_console.print(f"  [bold]{e['key']}[/bold] (v{e['version']}, {e['source']}): {e['value'][:100]}")
        if not entries:
            _rich_console.print("  [dim]空[/dim]")
        _rich_console.print()
    elif sub in ("send",) and arg:
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            _rich_console.print("\n  [dim]用法: /star send <目标Agent> <消息内容>[/dim]\n")
            return
        msg = bus.send("orchestrator", parts[0].strip(), parts[1].strip())
        _rich_console.print(f"\n  ✅ 已发送 → [cyan]{parts[0].strip()}[/cyan] ({msg.msg_id})\n")
    else:
        _rich_console.print(
            "\n[dim]用法: /star | /star inbox <Agent> | /star board | /star send <Agent> <内容>[/dim]\n"
        )


def _do_reload(core, providers_yaml, routing_config, system_prompt) -> None:
    """热加载配置：重新读 providers.yaml + skills。"""
    from tianshu.core.setup import load_user_keys
    from tianshu.core.config import load_providers, load_routing_config
    try:
        user_keys = load_user_keys()
        registry = load_providers(providers_yaml, extra_keys=user_keys)
        routing = load_routing_config(providers_yaml)
        core.setup(registry=registry, routing=routing, system_prompt=system_prompt,
                   db_path=str(_project_root / "tianshu.db"), skill_discover=True)
        _rich_console.print(f"  ✅ 已重载: {core.model_count} 模型, {core._tool_registry.count if core._tool_registry else '?'} 工具\n")
    except Exception as e:
        _rich_console.print(f"  [red]重载失败: {e}[/red]\n")


def _resolve_at_refs(user_input: str) -> str:
    """解析输入中的 @file.py 引用，读取文件内容注入上下文。

    支持格式:
      @file.py           → 读取整个文件
      @file.py:42        → 读取第 42 行周围 20 行
      @file.py:10-50     → 读取第 10-50 行
      @dir/              → 列出目录内容
    """
    import re
    from pathlib import Path

    pattern = re.compile(r"@([^\s]+)")
    matches = pattern.findall(user_input)
    if not matches:
        return user_input

    resolved = user_input
    added_contexts: list[str] = []

    for ref in matches:
        line_spec = ""
        if ":" in ref:
            path_str, line_spec = ref.rsplit(":", 1)
        else:
            path_str = ref

        p = Path(path_str).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p

        try:
            if not p.exists() and not ref.startswith("http"):
                continue

            if ref.startswith("http://") or ref.startswith("https://"):
                # @url → 抓取网页内容
                import httpx as _hx
                try:
                    resp = _hx.get(ref, timeout=10, follow_redirects=True)
                    content = resp.text[:3000]
                    added_contexts.append(f"🌐 {ref}\n{'─'*50}\n{content}")
                except Exception:
                    pass
            elif p.is_dir():
                entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:30]
                content = f"📂 {p}\n" + "\n".join(
                    f"  {'📁' if e.is_dir() else '📄'} {e.name}"
                    for e in entries
                )
                added_contexts.append(content)
            else:
                # 文件：读取内容
                text = p.read_text(encoding="utf-8", errors="replace")
                lines = text.splitlines()

                if line_spec:
                    # 解析行号范围
                    if "-" in line_spec:
                        parts = line_spec.split("-")
                        start = int(parts[0]) if parts[0] else 1
                        end = int(parts[1]) if len(parts) > 1 and parts[1] else start + 20
                    else:
                        n = int(line_spec)
                        start = max(1, n - 10)
                        end = min(len(lines), n + 10)

                    start = max(1, start)
                    end = min(len(lines), end)
                    selected = lines[start - 1:end]
                    content = "\n".join(
                        f"{i:4d} │ {line}"
                        for i, line in enumerate(selected, start=start)
                    )
                    header = f"📄 {p.name}:L{start}-L{end}"
                else:
                    content = text
                    header = f"📄 {p.name} ({len(lines)} 行)"

                content = f"{header}\n{'─' * 50}\n{content}"

            added_contexts.append(content)
        except Exception:
            continue

    if not added_contexts:
        return user_input

    context_block = "\n\n".join(added_contexts)
    return (
        f"{user_input}\n\n"
        f"[以下是从 @ 引用中读取的文件内容]\n"
        f"{context_block}"
    )


def _init_globals() -> None:
    """确保 sys.path 正确，编码修复。"""
    import sys
    from pathlib import Path
    _project_root = Path(__file__).resolve().parents[2]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    main()
