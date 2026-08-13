"""MCP Client Manager — 连接外部 MCP Server，工具自动注册到 ToolRegistry。

借鉴 Claude Code + OpenClaw native 的 MCP 集成模式。
使用官方 mcp SDK v2.x，支持 Streamable HTTP + stdio 两种 transport。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("tianshu.mcp")

# ── McpClientManager ─────────────────────────────────────────────────────────


class McpClientManager:
    """MCP 客户端管理器。

    管理多个 MCP Server 的连接生命周期：
    - 连接时自动发现工具并注册到 ToolRegistry
    - 工具调用通过命名前缀 mcp_{server}_{tool} 路由到正确的 client
    - 健康检查 + 指数退避自动重连
    - 支持热重载（disconnect_all → reconnect_all）

    用法:
        mgr = McpClientManager()

        # 加载配置 + 连接
        config = load_mcp_config("config/mcp.yaml")
        count = await mgr.connect_all(config.get("servers", {}), registry)

        # 工具调用（由 SkillExecutor 通过 handler 闭包调用）
        result = await mgr.call_tool("mcp_filesystem_read_file", {"path": "/tmp/x"})

        # 查看状态
        servers = mgr.list_servers()
    """

    # 工具调用超时（秒）
    TOOL_CALL_TIMEOUT = 60.0

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}          # server_name → mcp.Client
        self._tools: dict[str, _McpToolMeta] = {}   # tool_name → metadata
        self._registry: Any = None                   # ToolRegistry (set by setup)
        self._server_configs: dict[str, dict] = {}   # 原始配置（用于重连）
        self._errors: dict[str, str] = {}            # server_name → 最近错误
        self._health_task: asyncio.Task | None = None

    # ── 连接管理 ──────────────────────────────────────────────────────

    async def connect_all(
        self, servers: dict[str, dict], registry: Any
    ) -> int:
        """连接配置中的所有 MCP Server 并注册工具。

        Args:
            servers: {name: {transport, url/command, args, env, headers}}
            registry: ToolRegistry 实例

        Returns:
            成功注册的工具总数
        """
        self._registry = registry
        self._server_configs = servers
        self._errors.clear()

        total = 0
        for name, cfg in servers.items():
            try:
                count = await self.connect_server(name, cfg)
                total += count
            except (Exception, RuntimeError) as e:
                self._errors[name] = str(e)[:200]
                if not isinstance(e, RuntimeError):
                    logger.warning(f"MCP server '{name}' 连接失败: {e}")

        return total

    async def connect_server(self, name: str, config: dict) -> int:
        """连接单个 MCP Server 并注册其工具。

        Args:
            name: server 名称（配置中的 key）
            config: {transport, url/command, args?, env?, cwd?, headers?}

        Returns:
            注册的工具数量
        """
        # 如果已连接，先断开
        if name in self._clients:
            await self.disconnect_server(name)

        transport = config.get("transport", "http")
        self._errors.pop(name, None)

        # ── 创建 Client ──
        try:
            from mcp import Client
        except ImportError:
            raise ImportError(
                "mcp SDK 未安装。请运行: pip install 'tianshu[mcp]' 或 pip install 'mcp>=2.0'"
            )

        if transport == "stdio":
            command = config.get("command", "")
            args = config.get("args", [])
            env = config.get("env") or None
            cwd = config.get("cwd") or None

            from mcp import StdioServerParameters
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env,
                cwd=cwd,
            )
            self._clients[name] = Client(server_params)

        elif transport == "http":
            url = config.get("url", "")
            if not url:
                raise ValueError(f"MCP server '{name}': HTTP transport requires 'url'")
            # SDK v2: Client(url) for Streamable HTTP
            # headers/auth 通过 URL 或 OAuth2 处理，不在 Client 构造参数中
            self._clients[name] = Client(url)

        else:
            raise ValueError(
                f"MCP server '{name}': 不支持的 transport '{transport}'（支持: stdio, http）"
            )

        client = self._clients[name]

        # ── 发现工具并注册 ──
        count = 0
        try:
            await client.__aenter__()

            # MCP SDK v2: list_tools() 返回 ListToolsResult（有 .tools 属性）
            tools_result = await client.list_tools()
            tools = (
                tools_result.tools if hasattr(tools_result, "tools")
                else tools_result
            )

            for tool in tools:
                # MCP tool 对象属性: name, description, inputSchema
                tool_name = tool.name
                description = getattr(tool, "description", "") or ""
                input_schema = getattr(tool, "inputSchema", {}) or {}

                # 生成天枢工具名: mcp_{server}_{tool}
                tianshu_name = f"mcp_{name}_{tool_name}"

                # 创建 handler 闭包（默认参数避免 late-binding 问题）
                async def _mcp_handler(
                    _server=name, _tool=tool_name, **kwargs: Any,
                ) -> str:
                    return await self.call_tool(
                        f"mcp_{_server}_{_tool}", kwargs,
                    )

                # 注册到 ToolRegistry
                if self._registry:
                    from ..core.tool_registry import ToolInfo
                    self._registry.register(ToolInfo(
                        name=tianshu_name,
                        description=f"[MCP:{name}] {description}",
                        parameters=input_schema,
                        handler=_mcp_handler,
                        permission=2,  # 默认 WRITE（保守）
                        skill_name=f"mcp_{name}",
                        category="mcp",
                    ))

                # 记录元数据
                self._tools[tianshu_name] = _McpToolMeta(
                    server=name,
                    original_name=tool_name,
                    description=description,
                )
                count += 1

        except Exception:
            # 连接或发现失败 → 清理
            await self._close_client(name)
            raise

        return count

    async def disconnect_server(self, name: str) -> None:
        """断开指定 MCP Server 并清理已注册的工具。"""
        # 清理 ToolRegistry 中的工具
        prefix = f"mcp_{name}_"
        if self._registry and hasattr(self._registry, "unregister_prefix"):
            self._registry.unregister_prefix(prefix)

        # 清理工具元数据
        self._tools = {
            k: v for k, v in self._tools.items()
            if v.server != name
        }

        # 关闭 client
        await self._close_client(name)

    async def disconnect_all(self) -> None:
        """断开所有 MCP Server 连接。"""
        for name in list(self._clients.keys()):
            await self.disconnect_server(name)
        self._clients.clear()
        self._tools.clear()
        self._errors.clear()

    async def _close_client(self, name: str) -> None:
        """安全关闭 MCP Client。"""
        client = self._clients.pop(name, None)
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
            except (Exception, RuntimeError, BaseException):
                pass  # MCP SDK 在连接失败时有已知的 cancel scope bug
        self._errors.pop(name, None)

    # ── 工具调用 ──────────────────────────────────────────────────────

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """调用 MCP 工具并返回结果字符串。

        Args:
            tool_name: 天枢工具名 (mcp_{server}_{tool})
            arguments: 工具参数

        Returns:
            工具执行结果（字符串）
        """
        meta = self._tools.get(tool_name)
        if meta is None:
            self._record_call(tool_name, False)
            return f"未知 MCP 工具: {tool_name}"

        client = self._clients.get(meta.server)
        if client is None:
            self._record_call(tool_name, False)
            return f"MCP Server '{meta.server}' 未连接"

        try:
            result = await asyncio.wait_for(
                client.call_tool(meta.original_name, arguments),
                timeout=self.TOOL_CALL_TIMEOUT,
            )

            # MCP 结果有多种内容类型
            if hasattr(result, "structured_content") and result.structured_content:
                import json as _json
                output = _json.dumps(
                    result.structured_content, ensure_ascii=False, indent=2,
                )
            elif hasattr(result, "content") and result.content:
                parts = []
                for c in result.content:
                    if hasattr(c, "text"):
                        parts.append(str(c.text))
                    else:
                        parts.append(str(c))
                output = "\n".join(parts)
            else:
                output = str(result)

            self._record_call(tool_name, True)
            return output

        except asyncio.TimeoutError:
            self._record_call(tool_name, False)
            return (
                f"MCP 工具调用超时 [{tool_name}]: "
                f"{self.TOOL_CALL_TIMEOUT}s 无响应"
            )
        except Exception as e:
            self._record_call(tool_name, False)
            return f"MCP 工具调用失败 [{tool_name}]: {type(e).__name__}: {e}"

    # ── 查询 ──────────────────────────────────────────────────────────

    def list_servers(self) -> list[dict[str, Any]]:
        """列出所有已配置的 MCP Server 及其状态。"""
        result = []
        for name, cfg in self._server_configs.items():
            is_connected = name in self._clients
            tool_count = sum(
                1 for t in self._tools.values() if t.server == name
            )
            entry: dict[str, Any] = {
                "name": name,
                "transport": cfg.get("transport", "http"),
                "connected": is_connected,
                "tools": tool_count,
            }
            if name in self._errors:
                entry["error"] = self._errors[name]
            result.append(entry)
        # 加入已连接但不在配置中的（动态添加的）
        for name in self._clients:
            if name not in self._server_configs:
                tool_count = sum(
                    1 for t in self._tools.values() if t.server == name
                )
                result.append({
                    "name": name,
                    "transport": "dynamic",
                    "connected": True,
                    "tools": tool_count,
                })
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """列出所有 MCP 工具及其元数据。"""
        return [
            {
                "name": name,
                "server": meta.server,
                "original_name": meta.original_name,
                "description": meta.description,
            }
            for name, meta in self._tools.items()
        ]

    def last_errors(self) -> dict[str, str]:
        """返回最近一次连接失败的错误信息。"""
        return dict(self._errors)

    # ── 健康检查 ──────────────────────────────────────────────────────

    async def health_check(self) -> dict[str, bool]:
        """检查所有 MCP Server 的健康状态。"""
        status: dict[str, bool] = {}
        for name, _cfg in self._server_configs.items():
            if name not in self._clients:
                status[name] = False
                continue
            try:
                client = self._clients[name]
                await client.list_tools()
                status[name] = True
            except Exception:
                status[name] = False
        return status

    async def reconnect_server(self, name: str) -> bool:
        """重连指定 server（带指数退避）。

        Returns:
            True 如果重连成功
        """
        config = self._server_configs.get(name)
        if not config:
            return False

        max_retries = 5
        base_delay = 1.0  # 秒

        for attempt in range(max_retries):
            try:
                await self.disconnect_server(name)
                count = await self.connect_server(name, config)
                return count > 0
            except Exception:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # 1, 2, 4, 8, 16
                    await asyncio.sleep(min(delay, 30.0))

        return False

    # ── 工具调用计数 ──────────────────────────────────────────────────

    def _record_call(self, tool_name: str, success: bool) -> None:
        """向 ToolRegistry 汇报调用结果（用于 trigram 风险反馈）。"""
        if self._registry:
            tool_info = self._registry.get(tool_name)
            if tool_info:
                tool_info.call_count += 1
                if not success:
                    tool_info.error_count += 1


# ── 内部类型 ─────────────────────────────────────────────────────────────


class _McpToolMeta:
    """MCP 工具元数据——用于路由和查询。"""
    __slots__ = ("server", "original_name", "description")

    def __init__(self, server: str, original_name: str, description: str = "") -> None:
        self.server = server
        self.original_name = original_name
        self.description = description
