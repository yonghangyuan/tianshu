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
    """Rich 版帮助——由 CommandRegistry 驱动。"""
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
        idx = select_menu("选择默认模型", options)
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
        _rich_console.print(f"\n  ✅ 已恢复会话 [cyan]{session_id[:8]}[/cyan] ({len(ctx.messages)} 条消息)\n")

    elif sub == "delete" and len(parts) > 2:
        session_id = parts[2].strip()
        ok = store.delete(session_id)
        if ok:
            _rich_console.print(f"\n  ✅ 已删除会话 [cyan]{session_id[:8]}[/cyan]\n")
        else:
            _rich_console.print(f"\n  [red]会话未找到: {session_id[:8]}[/red]\n")

    elif sub == "new":
        ctx.session_id = f"sess_{int(time.time())}"
        ctx.messages.clear()
        ctx.metadata.clear()
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
    config_dir = _project_root / "config"
    providers_yaml = config_dir / "providers.yaml"
    soul_md = config_dir / "soul.md"

    if not providers_yaml.exists():
        _rich_console.print("[red]❌ config/providers.yaml 未找到[/red]")
        _rich_console.print("[dim]请从 config/providers.yaml.example 复制并填入 API Key[/dim]")
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

    def _mode_label(m: str) -> str:
        return {
            "normal": "[dim]⏵ normal[/dim]",
            "auto":   "[bold #a78bfa]⏵⏵ auto[/bold #a78bfa]",
            "plan":   "[bold cyan]⏵ plan[/bold cyan]",
        }.get(m, "⏵ ??")

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
        _rich_console.print(f"\r  {_mode_label(_mode)}  [dim](Shift+Tab / /mode 切换)[/dim]")
        return _mode

    input_handler = create_input_handler(
        commands=cmd_registry.command_names,
        model_names=model_names,
        mode_callback=_cycle_mode,
    )

    def _prompt_text():
        m = ctx.metadata.get("model_override", "")
        model_part = m if m else "天枢"
        return f"{_mode_plain(_mode)} {model_part}> "

    # ── 会话存储 ──
    from tianshu.memory.session_store import get_session_store
    session_store = get_session_store()

    _print_cmd_help()
    if default_model:
        _rich_console.print(f"  默认模型: [cyan]{default_model}[/cyan]")
    _rich_console.print()

    renderer = StreamRenderer()

    # ── 同步主循环 ──
    while True:
        try:
            user_input = input_handler.prompt(_prompt_text()).strip()
        except (EOFError, KeyboardInterrupt):
            _rich_console.print("\n[dim]再见[/dim]")
            break

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
            keys = run_setup_wizard()
            if keys:
                save_user_keys(keys)
                user_keys = load_user_keys()
                registry = load_providers(providers_yaml, extra_keys=user_keys)
                core.setup(registry=registry, routing=routing_config, system_prompt=system_prompt, db_path=str(_project_root / "tianshu.db"), skill_discover=True)
                _rich_console.print(f"\n  ✅ 已保存 {len(keys)} 个 Key。")
            continue
        elif user_input.startswith("model ") or user_input.startswith("/model "):
            _handle_model_command(user_input, core._registry, ctx)
            continue
        elif user_input in ("skills", "/skills"):
            _show_skills(core.skills.loader, core.skills.observer)
            continue
        elif user_input in ("help", "/help"):
            _print_cmd_help()
            continue
        elif user_input in ("think", "/think"):
            rc = core.last_reasoning
            if rc:
                from rich.panel import Panel as _P
                _rich_console.print(_P(rc[:3000], title="Thinking", border_style="dim"))
            else:
                _rich_console.print("[dim]  暂无推理内容[/dim]")
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
        elif user_input in ("clear", "/clear"):
            _rich_console.clear()
            continue

        # ── @file 文件引用解析 ──
        resolved_input = _resolve_at_refs(user_input)

        # ── 对话（每次独立 asyncio.run）──
        from tianshu.sdk.models import AgentRequest, ToolCallConfirm
        from tianshu.gateway.cli import _handle_confirm

        _rich_console.print(
            f"[bold #60a5fa]▸[/bold #60a5fa] {user_input}"
        )
        if resolved_input != user_input:
            _rich_console.print("  [dim]已读取 @ 引用的文件[/dim]")
        _rich_console.print()
        renderer.reset()

        async def _run_turn():
            async for event in core.run_stream(
                AgentRequest(input=resolved_input, task_type="conversation"),
                ctx=ctx,
            ):
                if isinstance(event, ToolCallConfirm):
                    _handle_confirm(event, core)
                else:
                    renderer.handle(event)

        try:
            asyncio.run(_run_turn())
        except KeyboardInterrupt:
            _rich_console.print("\n  [dim]已取消[/dim]")
        except Exception as e:
            msg = str(e)
            # 分类提示
            if "connect" in msg.lower() or "timeout" in msg.lower() or "refused" in msg.lower():
                _rich_console.print(f"[bold red]🌐 网络不通[/bold red] — 请检查网络或代理设置")
            elif "401" in msg or "403" in msg:
                _rich_console.print(f"[bold red]🔑 鉴权失败[/bold red] — 请用 /setup 重新配置 API Key")
            elif "429" in msg or "rate" in msg.lower():
                _rich_console.print(f"[bold red]⏳ 请求太频繁[/bold red] — 稍等几秒再试")
            elif "not set up" in msg.lower():
                _rich_console.print(f"[bold red]⚙️ 未初始化[/bold red] — 请检查 config/providers.yaml")
            else:
                _rich_console.print(f"[bold red]错误:[/bold red] {msg[:300]}")

        _rich_console.print()
        session_store.save(ctx, title=user_input[:50])

    _rich_console.print("[dim]天枢已关闭[/dim]")


def _show_tools(core) -> None:
    """显示所有已注册的工具。"""
    reg = core._tool_registry
    if not reg:
        _rich_console.print("[dim]ToolRegistry 未初始化[/dim]")
        return
    _rich_console.print()
    _rich_console.print(reg.stats())
    _rich_console.print()


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
        # 解析路径和行号
        line_spec = ""
        if ":" in ref:
            path_str, line_spec = ref.rsplit(":", 1)
        else:
            path_str = ref

        p = Path(path_str).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p

        try:
            if not p.exists():
                continue

            if p.is_dir():
                # 目录：列出内容
                entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:30]
                content = f"📂 {p}\n" + "\n".join(
                    f"  {'📁' if e.is_dir() else '📄'} {e.name}"
                    for e in entries
                )
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
