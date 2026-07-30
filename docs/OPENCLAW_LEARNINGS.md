# OpenClaw 架构分析 + 天枢借鉴评估

> 分析日期: 2026-07-30 | 基于 openclaw main 分支源码
>
> **核心原则**: 独立自主可控 · 遵循中国法律法规 · 数据主权本地化

---

## 一、前置审查：天枢的红线

以下 OpenClaw 能力**不借鉴、不实现、不在计划内**：

| OpenClaw 能力 | 为何不做 |
|---|---|
| 全球 CDN 部署 / Cloudflare R2 | 数据出境、境外存储 |
| 跨境消息通道（WhatsApp/Signal/Telegram 等 20+ 平台） | 数据经境外服务器 |
| 自动翻墙/代理工具 | 违反《网络安全法》 |
| Google/OpenAI API 默认路由 | 模型自主原则 |
| 远程 Node 配对（SSH 穿透防火墙） | 安全风险 + 合规风险 |
| 境外 Cloud 部署模板（fly.io/render.com） | 数据主权 |

---

## 二、OpenClaw 规模

| | OpenClaw | 天枢 |
|---|---|---|
| 语言 | TypeScript monorepo | Python 单体 |
| 源文件 | 23,946 .ts/.tsx | 52 .py |
| 测试 | ~8000 test 文件 | 53 test |
| 核心模块 | 150+ 目录 | 6 包 |

---

## 三、值得借鉴的架构设计（合规前提下）

### 3.1 Plugin/Extension SDK

**OpenClaw 做法**: `plugin-sdk/` 定义干净 API。所有功能（通道、工具、记忆）都是插件。Core 保持插件无关，插件通过 manifest 注册。

**天枢现状**: Skill 系统方向对了，但没有正式 SDK。`PluginManager` 是简单的文件夹扫描。

**借鉴方案**: 定义 Skill SDK 规范——
- 每个 Skill = `SKILL.md`（声明）+ 可选的 `handler.py`（代码）
- 统一的权限声明（SAFE/READ/WRITE/DANGER）
- 启动时自动发现 + 注册

**合规**: ✅ 纯本地架构，无外部依赖。

### 3.2 Gateway 即控制平面

**OpenClaw 做法**: Gateway 是 WebSocket 服务器，统一管理 Agent 生命周期、会话存储、模型目录、Cron、健康检查。

**天枢现状**: `gateway/` 有骨架但未跑通。Agent 生命周期由 `AgentCore.setup()` 管理，会话由 `SessionStore` 管理——分散在各处。

**借鉴方案**: 
- 统一 Gateway 入口（HTTP + WebSocket）
- Agent 生命周期 API（create/start/stop/delete）
- 统一会话路由（哪个 session 用哪个 model）

**合规**: ✅ 在国内服务器部署即可。

### 3.3 工具注册中心 + 能力过滤

**OpenClaw 做法**: 按 mode（plan/agent/yolo）过滤工具。Plan 模式只暴露 READ_ONLY 工具。

**天枢现状**: 已有 `_get_tools()` 但无模式过滤。Plan 模式只是 prompt 暗示。

**借鉴方案**:
- `ToolRegistry` 集中注册所有工具
- 按 PermissionLevel 过滤（SAFE/READ/WRITE/DANGER）
- 按 mode 过滤：normal 全工具，plan 只读，auto 全工具无确认

**合规**: ✅

### 3.4 上下文引擎

**OpenClaw 做法**: `context-engine/` 独立模块，管理 prompt 组装、记忆注入、系统提示拼装。

**天枢现状**: `_build_messages()` 200 行，混在 `service.py` 里。

**借鉴方案**: 独立 `ContextEngine` 类——负责 prompt 组装 + 系统提示拼装 + 记忆注入 + 上下文压缩。

**合规**: ✅

---

## 四、不借鉴的设计

| OpenClaw 设计 | 原因 |
|---|---|
| SQLite-only state 强制 | 天枢已有 SQLite + Markdown 混合，够用 |
| TypeScript monorepo 结构 | Python 项目不需要 |
| Fleet/Worker pool 多 Agent 编排 | 天枢目前单 Agent 够用，先不做分布式 |
| Canvas/A2UI 可视化 | 前端工作量太大，暂不需要 |
| macOS/iOS/Android native apps | Windows 优先 |

---

## 五、执行优先级

```
P0: Skill SDK 规范 + ToolRegistry 集中注册 (半天)
P1: Gateway 统一控制平面 (1-2天)
P1: ContextEngine 独立化 (半天)
P3: 模式工具过滤（plan 模式只暴露只读工具）
```

---

## 六、总结

OpenClaw 是 TypeScript 单体仓库 + 插件生态 + 多通道网关。天枢不需要也不可能复制其规模，但可以学习三个核心设计：

1. **插件 SDK**——让 Skills 成为标准化、可插拔的模块
2. **Gateway 控制平面**——统一管理 Agent 生命周期、会话、工具
3. **能力过滤**——根据模式和安全级别动态暴露工具

所有借鉴限于本地架构和国内生态，不涉及：
- 跨境数据传输
- 绕过网络审查
- 境外云服务依赖
- 非国内模型 API 调用
