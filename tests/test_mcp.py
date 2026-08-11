"""MCP 集成测试。

测试 McpClientManager 生命周期：配置解析、连接、工具注册、调用、断开。
使用 mock mcp.Client 避免依赖真实的 MCP server。
"""

from __future__ import annotations

import pytest


# ── Mock MCP Client ───────────────────────────────────────────────────────


class _MockTool:
    """模拟 MCP tool 对象。"""
    def __init__(self, name: str, description: str = "", input_schema: dict | None = None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object", "properties": {}}


class _MockCallToolResult:
    """模拟 MCP call_tool 返回值。"""
    def __init__(self, text: str = "", structured: dict | None = None):
        self.content = [_MockTextContent(text)] if text else []
        self.structured_content = structured


class _MockTextContent:
    def __init__(self, text: str):
        self.text = text


class _MockClient:
    """模拟 mcp.Client。"""

    def __init__(self, tools: list[_MockTool] | None = None,
                 fail_on: str | None = None):
        self._tools = tools or []
        self._fail_on = fail_on  # "connect" | "list_tools" | "call_tool"
        self._connected = False
        self._closed = False

    async def __aenter__(self):
        if self._fail_on == "connect":
            raise ConnectionError("模拟连接失败")
        self._connected = True
        return self

    async def __aexit__(self, *args):
        self._connected = False
        self._closed = True

    async def list_tools(self):
        if self._fail_on == "list_tools":
            raise RuntimeError("模拟工具发现失败")
        return self._tools

    async def call_tool(self, name: str, arguments: dict):
        if self._fail_on == "call_tool":
            raise RuntimeError("模拟工具调用失败")
        return _MockCallToolResult(
            text=f"结果: {name}({arguments})",
            structured={"tool": name, "args": arguments},
        )


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def registry():
    """创建一个空的 ToolRegistry。"""
    from tianshu.core.tool_registry import ToolRegistry
    return ToolRegistry()


@pytest.fixture
def mock_mcp_module(monkeypatch):
    """Mock mcp 模块，注入 _MockClient。"""
    import sys
    mock_mod = type(sys)("mcp")
    mock_mod.Client = _MockClient
    monkeypatch.setitem(sys.modules, "mcp", mock_mod)
    return mock_mod


# ── Config 加载测试 ───────────────────────────────────────────────────────


class TestLoadMcpConfig:
    """测试 load_mcp_config() 配置解析。"""

    def test_load_missing_file(self):
        """配置文件不存在时返回空 dict。"""
        from tianshu.core.config import load_mcp_config
        config = load_mcp_config("config/nonexistent_mcp.yaml")
        assert config == {}

    def test_load_valid_config(self, tmp_path):
        """解析有效的 YAML 配置。"""
        import yaml
        from tianshu.core.config import load_mcp_config

        config_data = {
            "servers": {
                "test_server": {
                    "transport": "http",
                    "url": "https://example.com/mcp",
                }
            }
        }
        config_file = tmp_path / "mcp.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        config = load_mcp_config(str(config_file))
        assert "servers" in config
        assert config["servers"]["test_server"]["url"] == "https://example.com/mcp"

    def test_env_var_resolution(self, tmp_path, monkeypatch):
        """${ENV_VAR} 环境变量替换。"""
        import yaml
        from tianshu.core.config import load_mcp_config

        monkeypatch.setenv("TEST_KEY", "secret_token")
        config_data = {
            "servers": {
                "test_server": {
                    "transport": "http",
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer ${TEST_KEY}"},
                }
            }
        }
        config_file = tmp_path / "mcp.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        config = load_mcp_config(str(config_file))
        headers = config["servers"]["test_server"]["headers"]
        assert headers["Authorization"] == "Bearer secret_token"


# ── McpClientManager 测试 ────────────────────────────────────────────────


class TestMcpClientManager:
    """测试 McpClientManager 核心生命周期。"""

    @pytest.fixture
    def manager(self):
        from tianshu.renyao.mcp_client import McpClientManager
        return McpClientManager()

    def test_init_empty(self, manager):
        """初始化时没有任何连接。"""
        assert manager.list_servers() == []
        assert manager.list_tools() == []

    @pytest.mark.asyncio
    async def test_connect_http_server(self, manager, registry, mock_mcp_module):
        """连接 HTTP MCP server，发现工具并注册。"""
        mock_mcp_module.Client = lambda url, headers=None, **kw: _MockClient(
            tools=[
                _MockTool("search", "Search the web",
                         {"type": "object", "properties": {"query": {"type": "string"}}}),
                _MockTool("read", "Read a page",
                         {"type": "object", "properties": {"url": {"type": "string"}}}),
            ],
        )

        config = {
            "transport": "http",
            "url": "https://example.com/mcp",
        }
        count = await manager.connect_server("test", config)

        assert count == 2
        # list_servers 会包含已连接但不在 _server_configs 中的（标记为 dynamic）
        servers = manager.list_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "test"
        assert servers[0]["transport"] == "dynamic"
        assert servers[0]["connected"] is True

    @pytest.mark.asyncio
    async def test_connect_and_call_tool(self, manager, registry, mock_mcp_module):
        """连接 server 后可以通过 call_tool 调用工具。"""
        mock_mcp_module.Client = lambda url, headers=None, **kw: _MockClient(
            tools=[_MockTool("add", "Add two numbers",
                            {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}})],
        )

        # 需要先设置 registry 以便 connect_server 注册工具
        manager._registry = registry
        config = {"transport": "http", "url": "https://example.com/mcp"}
        await manager.connect_server("calc", config)

        result = await manager.call_tool("mcp_calc_add", {"a": 1, "b": 2})
        assert "add" in result
        assert "1" in result

    @pytest.mark.asyncio
    async def test_connect_all_multiple_servers(self, manager, registry, mock_mcp_module):
        """同时连接多个 server。"""
        # Mock Client: 接收 StdioServerParameters (stdio) 或 str (HTTP)
        def _make_client(server, **kw):
            # HTTP: server 是 URL 字符串
            if isinstance(server, str):
                if "weather" in server:
                    return _MockClient(tools=[
                        _MockTool("forecast", "Weather forecast", {}),
                    ])
                elif "news" in server:
                    return _MockClient(tools=[
                        _MockTool("headlines", "News headlines", {}),
                        _MockTool("search", "Search news", {}),
                    ])
            # stdio: server 是 StdioServerParameters
            else:
                if hasattr(server, 'command') and 'filesystem' in str(getattr(server, 'args', [])):
                    return _MockClient(tools=[
                        _MockTool("read_file", "Read file", {}),
                        _MockTool("write_file", "Write file", {}),
                    ])
            return _MockClient(tools=[])

        mock_mcp_module.Client = _make_client

        servers = {
            "weather": {"transport": "http", "url": "https://weather.example.com/mcp"},
            "news": {"transport": "http", "url": "https://news.example.com/mcp"},
        }
        manager._registry = registry
        manager._server_configs = servers

        count = await manager.connect_all(servers, registry)
        assert count == 3  # 1 + 2 tools

    @pytest.mark.asyncio
    async def test_disconnect_cleans_tools(self, manager, registry, mock_mcp_module):
        """断开连接后清理注册的工具。"""
        mock_mcp_module.Client = lambda url, headers=None, **kw: _MockClient(
            tools=[_MockTool("test_tool", "Test", {})],
        )

        manager._registry = registry
        config = {"transport": "http", "url": "https://example.com/mcp"}
        await manager.connect_server("test", config)

        # 验证工具已注册
        assert len(manager.list_tools()) == 1

        # 断开
        await manager.disconnect_server("test")
        assert len(manager.list_tools()) == 0

    @pytest.mark.asyncio
    async def test_call_unknown_tool_returns_error(self, manager):
        """调用未知工具返回错误消息。"""
        result = await manager.call_tool("mcp_nonexistent_tool", {})
        assert "未知" in result

    @pytest.mark.asyncio
    async def test_connect_server_error_handling(self, manager, registry, mock_mcp_module):
        """server 连接失败时不应崩溃。"""
        mock_mcp_module.Client = lambda url, headers=None, **kw: _MockClient(
            tools=[], fail_on="connect",
        )

        with pytest.raises(ConnectionError, match="模拟连接失败"):
            await manager.connect_server("bad_server",
                                        {"transport": "http", "url": "https://bad.example.com"})

    @pytest.mark.asyncio
    async def test_list_servers_status(self, manager, registry):
        """list_servers 返回 server 状态。"""
        manager._server_configs = {
            "filesystem": {"transport": "stdio", "command": "npx", "args": ["-y", "server"]},
            "tavily": {"transport": "http", "url": "https://mcp.tavily.com/mcp"},
        }
        servers = manager.list_servers()
        assert len(servers) == 2
        assert servers[0]["connected"] is False  # 未连接
        assert servers[1]["connected"] is False


# ── ToolRegistry unregister_prefix 测试 ──────────────────────────────────


class TestUnregisterPrefix:
    """测试 ToolRegistry.unregister_prefix()。"""

    def test_unregister_by_prefix(self, registry):
        """按前缀清理工具。"""
        from tianshu.core.tool_registry import ToolInfo

        registry.register(ToolInfo("mcp_a_tool1", "desc", {}, None, skill_name="mcp_a"))
        registry.register(ToolInfo("mcp_a_tool2", "desc", {}, None, skill_name="mcp_a"))
        registry.register(ToolInfo("mcp_b_tool1", "desc", {}, None, skill_name="mcp_b"))
        registry.register(ToolInfo("core_tool", "desc", {}, None, skill_name="core"))

        removed = registry.unregister_prefix("mcp_a_")
        assert removed == 2
        assert registry.get("mcp_a_tool1") is None
        assert registry.get("mcp_a_tool2") is None
        assert registry.get("mcp_b_tool1") is not None
        assert registry.get("core_tool") is not None

    def test_unregister_no_match(self, registry):
        """无匹配时返回 0。"""
        removed = registry.unregister_prefix("mcp_nonexistent_")
        assert removed == 0
