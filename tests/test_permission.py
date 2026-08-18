"""Phase 3: Permission & Safety 测试。"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root / "src"))

from tianshu.sdk.models import PermissionLevel
from tianshu.core.service import AgentCore


class TestPermissionLevel:
    """测试权限级别枚举。"""

    def test_permission_level_values(self):
        assert PermissionLevel.SAFE == 0
        assert PermissionLevel.READ == 1
        assert PermissionLevel.WRITE == 2
        assert PermissionLevel.DANGER == 3

    def test_permission_ordering(self):
        assert PermissionLevel.SAFE < PermissionLevel.READ
        assert PermissionLevel.READ < PermissionLevel.WRITE
        assert PermissionLevel.WRITE < PermissionLevel.DANGER


class TestToolPermission:
    """测试 _get_tool_permission() 映射。"""

    def test_safe_tools(self):
        core = AgentCore()
        assert core._get_tool_permission("web_search") == 0
        assert core._get_tool_permission("remember_fact") == 0
        assert core._get_tool_permission("recall_memory") == 0
        assert core._get_tool_permission("get_model_status") == 0

    def test_write_tools(self):
        core = AgentCore()
        assert core._get_tool_permission("shell_exec") == 2
        assert core._get_tool_permission("download_pdf") == 2
        assert core._get_tool_permission("write_paper_notes") == 2

    def test_preset_tools_write_level(self):
        """edit_file / run_code 必须映射为 WRITE=2，否则确认逻辑全错。"""
        core = AgentCore()
        assert core._get_tool_permission("edit_file") == 2
        assert core._get_tool_permission("run_code") == 2

    def test_write_prefix_match(self):
        core = AgentCore()
        assert core._get_tool_permission("write_config") == 2
        assert core._get_tool_permission("download_model") == 2

    def test_read_prefix_match(self):
        core = AgentCore()
        assert core._get_tool_permission("search_arxiv") == 0
        assert core._get_tool_permission("read_file") == 0
        assert core._get_tool_permission("get_user_info") == 0

    def test_unknown_defaults_to_read(self):
        core = AgentCore()
        assert core._get_tool_permission("some_unknown_tool") == 1

    def test_whitelist_bypass(self):
        """白名单中的工具应跳过确认。"""
        core = AgentCore()
        core._permission_whitelist = {"shell_exec"}
        perm = core._get_tool_permission("shell_exec")
        # 权限仍为 WRITE，但白名单由 run_stream() 检查
        assert perm == 2


class TestConfirmMechanism:
    """测试 confirm_tool() 机制。"""

    def test_confirm_allowed(self):
        import asyncio
        core = AgentCore()
        event = asyncio.Event()
        core._confirm_pending = event
        core.confirm_tool(True)
        assert core._confirm_allowed is True
        assert event.is_set()

    def test_confirm_denied(self):
        import asyncio
        core = AgentCore()
        event = asyncio.Event()
        core._confirm_pending = event
        core.confirm_tool(False)
        assert core._confirm_allowed is False
        assert event.is_set()
