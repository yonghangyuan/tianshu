"""Import-boundary 源码测试 — 防止热启动路径导入重模块。

借鉴 OpenClaw server-import-boundary.test.ts:
AST 静态扫描，不实际导入。CI 自动防退化。
"""

import ast
from pathlib import Path

# 热启动路径入口文件
HOT_PATH_FILES = [
    "src/tianshu/main.py",           # CLI 入口
    "src/tianshu/gateway/server.py", # Server 入口
]

# 允许在热路径中导入的轻量模块（启动时必须的）
ALLOWED_HOT_IMPORTS = {
    "tianshu", "tianshu.core", "tianshu.core.config",
    "tianshu.core.setup", "tianshu.core.service",
    "tianshu.core.commands", "tianshu.core.input",
    "tianshu.core.menu", "tianshu.core.splash",
    "tianshu.core.status", "tianshu.core.init",
    "tianshu.core.context_engine",
    "tianshu.sdk", "tianshu.sdk.models", "tianshu.sdk.trigram",
    "tianshu.sdk.provider",
    "tianshu.diyao", "tianshu.diyao.providers",
    "tianshu.diyao.providers.base", "tianshu.diyao.providers.deepseek",
    "tianshu.diyao.providers.registry",
    "tianshu.gateway", "tianshu.gateway.cli",
    "tianshu.gateway.server",
    "tianshu.renyao", "tianshu.renyao.skills",
    "tianshu.renyao.skills.manifest",
    "tianshu.renyao.skills.learn",
    "tianshu.renyao.orchestrator",
    "tianshu.tianyao", "tianshu.tianyao.service",
    "tianshu.tianyao.scheduler",
    "tianshu.tianyao.agent_scheduler",
    "tianshu.memory", "tianshu.memory.service",
    "tianshu.memory.provider",
    "tianshu.rag", "tianshu.rag.service",
    "tianshu.core.tool_registry", "tianshu.core.policy_engine",
    "tianshu.core.presets", "tianshu.core.ptc",
    "tianshu.core.planner", "tianshu.core.router",
    "tianshu.core.db", "tianshu.core.turn_machine",
    "tianshu.diyao.sandbox",
    "tianshu.diyao.providers.doubao",
    "tianshu.diyao.providers.generic",
    "tianshu.renyao.skills.base", "tianshu.renyao.skills.loader",
    "tianshu.renyao.skills.plugin",
    "tianshu.renyao.skills.observer",
    "tianshu.renyao.skills.service",
    "tianshu.renyao.skills.executor",
    "tianshu.tianyao.audit", "tianshu.tianyao.provenance",
    "tianshu.sdk", "tianshu.sdk.models", "tianshu.sdk.trigram",
    "tianshu.gateway.tui", "tianshu.gateway.feishu",
    "tianshu.gateway.wechat", "tianshu.gateway.qqbot",
    "tianshu.gateway.wechat_mp",
    "tianshu.memory.session_store",
    "pathlib", "sys", "os", "json", "time", "typing", "asyncio",
    "argparse", "hashlib", "re", "uuid", "math", "yaml",
    "rich", "rich.console", "rich.table", "rich.markdown",
    "rich.panel", "rich.text", "rich.live", "rich.layout", "rich.box",
    "fastapi", "uvicorn", "httpx", "aiosqlite", "pydantic",
    "__future__", "contextlib", "importlib", "inspect", "ast",
    "dataclasses", "enum", "abc", "secrets",
}


def _extract_imports(file_path: Path) -> list[tuple[str, int]]:
    """AST 提取文件的顶层 import 语句。"""
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    imports: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                # 跳过相对导入 (from .xxx import ...)
                if node.level == 0:
                    imports.append((node.module, node.lineno))
                # 相对导入也记录（延迟加载相关）
                else:
                    base = str(file_path.parent).replace("\\", "/")
                    # 跳过——这是内部相对导入

    return imports


class TestImportBoundary:
    """热启动路径不应导入不在白名单中的模块。"""

    def test_cli_hot_path_only_allowed_imports(self):
        """CLI 入口不应导入重模块。"""
        root = Path(__file__).resolve().parents[1]
        cli_file = root / "src" / "tianshu" / "main.py"
        if not cli_file.exists():
            return  # 路径不同——跳过

        imports = _extract_imports(cli_file)
        violations: list[str] = []

        for module, line in imports:
            # 只检查 tianshu 内部导入
            if not module.startswith("tianshu"):
                continue
            if module not in ALLOWED_HOT_IMPORTS:
                violations.append(f"  {cli_file.name}:{line}: import {module}")

        assert not violations, (
            f"CLI 热路径导入了 {len(violations)} 个不在白名单的模块:\n"
            + "\n".join(violations[:10])
        )

    def test_server_hot_path_only_allowed_imports(self):
        """Server 入口不应导入重模块。"""
        root = Path(__file__).resolve().parents[1]
        srv_file = root / "src" / "tianshu" / "gateway" / "server.py"
        if not srv_file.exists():
            return

        imports = _extract_imports(srv_file)
        violations: list[str] = []

        for module, line in imports:
            if not module.startswith("tianshu"):
                continue
            if module not in ALLOWED_HOT_IMPORTS:
                violations.append(f"  {srv_file.name}:{line}: import {module}")

        assert not violations, (
            f"Server 热路径导入了 {len(violations)} 个不在白名单的模块:\n"
            + "\n".join(violations[:10])
        )

    def test_skills_imported_via_manifest_only(self):
        """Skill handler 不应在启动时直接导入——应通过 manifest 延迟加载。"""
        root = Path(__file__).resolve().parents[1]
        main_file = root / "src" / "tianshu" / "main.py"
        server_file = root / "src" / "tianshu" / "gateway" / "server.py"

        skill_imports = {
            "tianshu.renyao.skills.web_search",
            "tianshu.renyao.skills.browser",
            "tianshu.renyao.skills.code_assist",
            "tianshu.renyao.skills.file_ops",
            "tianshu.renyao.skills.image_gen",
            "tianshu.renyao.skills.intel",
            "tianshu.renyao.skills.paper_radar",
            "tianshu.renyao.skills.schedule",
            "tianshu.renyao.skills.shell",
            "tianshu.renyao.skills.translate",
            "tianshu.renyao.skills.trend_track",
            "tianshu.renyao.skills.rag",
        }

        for hot_file in [main_file, server_file]:
            if not hot_file.exists():
                continue
            imports = _extract_imports(hot_file)
            for module, line in imports:
                assert module not in skill_imports, (
                    f"{hot_file.name}:{line}: Skill {module} 在热路径直接导入！"
                    f"应通过 manifest 延迟加载。"
                )
