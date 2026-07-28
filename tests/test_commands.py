"""Phase 2: Command Registry 测试。"""

import pytest
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root / "src"))

from tianshu.core.commands import CommandRegistry, CommandContext, get_registry


class TestCommandRegistry:
    """测试 CommandRegistry。"""

    def test_register_and_find_exact(self):
        reg = CommandRegistry()

        @reg.register("test", aliases=["/test"], description="A test command")
        async def cmd_test(ctx: CommandContext) -> None:
            ctx.print("test")

        cmd = reg.find("test")
        assert cmd is not None
        assert cmd.name == "test"

    def test_find_by_alias(self):
        reg = CommandRegistry()

        @reg.register("hello", aliases=["/hello", "--hello"])
        async def cmd_hello(ctx: CommandContext) -> None:
            pass

        assert reg.find("/hello") is not None
        assert reg.find("--hello") is not None

    def test_find_fuzzy_match(self):
        reg = CommandRegistry()

        @reg.register("models", aliases=["/models"])
        async def cmd_models(ctx: CommandContext) -> None:
            pass

        cmd = reg.find("mod")
        assert cmd is not None
        assert cmd.name == "models"

    def test_find_missing(self):
        reg = CommandRegistry()
        assert reg.find("nonexistent") is None

    def test_find_ambiguous_returns_none(self):
        reg = CommandRegistry()

        @reg.register("models")
        async def _m(ctx): pass

        @reg.register("model")
        async def _d(ctx): pass

        # "mod" matches both → ambiguous → None
        assert reg.find("mod") is None

    def test_command_names(self):
        reg = CommandRegistry()

        @reg.register("help", aliases=["/help", "--help"])
        async def _h(ctx): pass

        @reg.register("models", aliases=["/models"])
        async def _m(ctx): pass

        names = reg.command_names
        assert "/help" in names
        assert "/models" in names
        assert "--help" in names

    @pytest.mark.asyncio
    async def test_handle_executes_handler(self):
        reg = CommandRegistry()
        executed = []

        @reg.register("ping")
        async def cmd_ping(ctx: CommandContext) -> None:
            executed.append("pong")

        ctx = CommandContext()
        result = await reg.handle("/ping", ctx)
        assert result is True
        assert executed == ["pong"]

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self):
        reg = CommandRegistry()
        result = await reg.handle("/unknown")
        assert result is False

    def test_get_registry_singleton(self):
        reg1 = get_registry()
        reg2 = get_registry()
        assert reg1 is reg2
        # 应包含内置命令
        assert reg1.find("help") is not None
        assert reg1.find("models") is not None
        assert reg1.find("exit") is not None
