"""统一初始化——CLI / Server / TUI 共享同一段 _init_core()。

消除 CLI 和 Server 各自维护一套初始化逻辑的现状。
"""

from __future__ import annotations

from pathlib import Path

from .config import load_providers, load_routing_config
from .setup import load_user_keys
from .service import AgentCore


def init_core(
    project_root: str | Path | None = None,
) -> AgentCore:
    """统一的 AgentCore 初始化——所有 Gateway 共用一个入口。

    自动从 config/providers.yaml 加载模型，
    从 ~/.tianshu/ 加载用户 Key，
    扫描 skills、加载插件、初始化调度器。

    Returns:
        已 setup 的 AgentCore 实例
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parents[3]

    root = Path(project_root)
    providers_yaml = root / "config" / "providers.yaml"
    soul_md = root / "config" / "soul.md"

    if not providers_yaml.exists():
        raise FileNotFoundError(f"config/providers.yaml not found at {providers_yaml}")

    user_keys = load_user_keys()
    registry = load_providers(providers_yaml, extra_keys=user_keys)
    routing = load_routing_config(providers_yaml)
    system_prompt = soul_md.read_text(encoding="utf-8") if soul_md.exists() else ""

    core = AgentCore()
    core.setup(
        registry=registry,
        routing=routing,
        system_prompt=system_prompt,
        db_path=str(root / "tianshu.db"),
        skill_discover=True,
    )
    return core
