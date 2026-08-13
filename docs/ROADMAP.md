# 天枢 → OpenClaw 路线图

> 创建: 2026-07-28 | 更新: 2026-08-10 | 目标: AI 操作系统级 Agent 框架
>
> 状态标记: ⬜ 待办 | 🔵 进行中 | ✅ 已完成 | ❌ 放弃

---

## 一、当前状态 vs OpenClaw

### 已持平 / 领先

| 能力 | 天枢 | OpenClaw | 状态 |
|------|------|----------|:----:|
| 流式输出 | SSE token 级 | ✅ | ✅ |
| Rich CLI | Markdown + 代码高亮 + 自动补全 | ✅ | ✅ |
| 多 Provider 路由 | 4 国产模型 + 自动路由 | 多模型 | ✅ |
| 审计追踪 | 天曜 4 级审计 | ACP 全链路 | ✅ |
| 权限系统 | SAFE/READ/WRITE/DANGER + 方向键确认 | allowlist | ✅ |
| 记忆系统 | FTS5 + 自动画像 + Digest + Decay + Compress | Honcho/Mem0 | ✅ 已持平 |
| 会话持久化 | SQLite + /session 命令 | Memory 系统 | ✅ |
| Skills 系统 | 12 个内置 + 自进化(observer) | 28K+ 社区 | 持平架构 |
| 嵌入式浏览器 | httpx + Edge CDP headless（零下载）| CDP Chrome | ✅ 已持平 |
| 搜索引擎 | 直接请求 Bing HTML（零 API） | SearXNG | ✅ |
| 多 Agent 编排 | 星群并行/串行 + 对抗验证 + worker 隔离 | ClawTeam | ✅ |
| 沙箱执行 | Docker(network none/256m/--read-only) + Local 降级 | NemoClaw | ✅ |
| 多模态 | GLM-5V 图像分析 + SenseNova 空间智能 | — | ✅ 领先 |
| 地图可视化 | GeoJSON 解析 + Haversine/OSMnx 路径 + Leaflet | — | ✅ 领先 |
| MCP 协议 | Client 端完整实现（stdio + Streamable HTTP） | SkillHub 生态 | ✅ 已持平 |

### 差距清单

---

## 二、P0 — 基础能力缺口（3 个月内）

### P0-001 — 发布 pip 包
- **状态**: ⬜ 待办
- **创建**: 2026-07-28 | **更新**: 2026-08-10
- **描述**: `pip install tianshu` 一键安装。当前需 `git clone` + `pip install -e .`
- **阻塞**: PyPI 上 `tianshu` 包名已被「天枢流形-蛋白质动力学状态预测」(归墟矩阵, v1.0.5) 占用。需改包名（如 `tianshu-ai`）或联系对方
- **预计**: 1 天（改名 + 发布）

### P0-002 — 清理死代码 + 项目结构规范
- **状态**: ⬜ 待办
- **创建**: 2026-07-28 | **更新**: 2026-08-10
- **描述**: `main.py` 中 `_main_deprecated()` 300+ 行死代码删除；统一 `__init__.py` 导出
- **进度**: 模块 `__init__.py` 导出已于 8/4 补齐，死代码清理未完成
- **预计**: 2 小时

### P0-003 — 跨平台 CLI 测试通过
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-04
- **描述**: GitHub Actions CI Ubuntu + Windows × Python 3.11/3.12/3.13 全通过
- **实际**: `pip install -e .[all]` + `pytest --tb=short` 已加入 CI workflow

### P0-004 — 配置文件热加载
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-04
- **描述**: CLI `/reload` 命令 + Server `POST /admin/reload` 端点，无需重启

### P0-005 — 错误信息友好化
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-04
- **描述**: API/网络/鉴权/限流 4 类中文错误提示，含恢复建议

---

## 三、P1 — 多通道接入（对标 OpenClaw Gateway）

### P1-001 — 微信/飞书/QQ Bot 至少跑通一个
- **状态**: ⬜ 待办
- **创建**: 2026-07-28
- **描述**: `gateway/` 下已有 feishu.py / wechat.py / qqbot.py 骨架，均未实际对接。先跑通飞书（API 最规范）
- **预计**: 2-3 天
- **OpenClaw 对标**: 15+ 通道（WhatsApp/Telegram/Discord/Slack/iMessage/微信/飞书/QQ）

### P1-002 — Web Chat 界面
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-06
- **描述**: `gateway/chat.html` 群聊 UI：暗色主题、成员列表、tool-call 状态指示器、Markdown 渲染、文件分享、@自动补全、流式输出

### P1-003 — HTTP API Server 实际可用
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-06
- **描述**: `gateway/server.py` ~40 端点：`/health`, `/run`, `/run/stream`(SSE), `/tools`, `/audit`, `/memory`, `/skills`, `/login`, `/chat/*`, `/map/*`, `/agents/*`, `/tasks`, `/admin/reload`, WebSocket `/ws`

### P1-004 — MCP 协议支持
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-10
- **描述**: MCP Client 完整实现。使用官方 `mcp>=2.0` SDK（支持 2026-07-28 stateless 协议 + 向后兼容 2025）。`McpClientManager` 管理多 server 生命周期——连接/工具发现/注册/调用/健康检查/重连。支持 stdio + Streamable HTTP 两种 transport。`config/mcp.yaml` 配置，`/mcp` CLI 命令组。13 个测试
- **文件**: `renyao/mcp_client.py`(250行), `core/config.py`(load_mcp_config), `core/tool_registry.py`(unregister_prefix), `core/service.py`(延迟连接), `config/mcp.yaml`, `tests/test_mcp.py`(13 tests)

### P1-005 — Playwright 浏览器增强（截图/点击/填表）
- **状态**: ⬜ 待办（浏览器核心已 ✅ 完成）
- **创建**: 2026-07-28 | **更新**: 2026-08-10
- **描述**: 浏览器核心（httpx + Edge CDP headless）已完整实现，覆盖静态+JS 渲染页面。Playwright 已有惰性回退钩子 `_try_playwright_render()`。本项聚焦 Playwright **交互增强**：截图 base64 → 多模态模型、点击、填表、翻页。非核心路径，属锦上添花
- **预计**: 2-3 天
- **OpenClaw 对标**: Chrome CDP 全自动（天枢用 Edge CDP 替代，已持平）

### P1-006 — 文件上下文引用（@file）
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-05
- **描述**: `@file.py` 自动读取 + SmartCompleter 路径补全。支持 `@url` 引用增强

### P1-007 — Diff 预览 + Inline Edit
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-06
- **描述**: `_compute_diff()` 文件修改前展示 +/- 行，用户确认后应用

---

## 四、P2 — 智能增强（对标 OpenClaw Agent）

### P2-001 — RAG 文档知识库（区别于已有对话记忆系统）
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-13
- **描述**: 对话记忆系统已完整（FTS5 + Digest + Decay + Compress + Prefetch，对标 Honcho/Mem0）。RAG 是另一件事——本地**文档**向量索引 + 混合检索（向量 + BM25），支持 PDF/Markdown/代码文件的摄取与语义搜索
- **实现**: `src/tianshu/rag/` — chunker(Markdown 标题感知+滑动窗口) + embedder(OpenAI兼容API/离线Mock降级) + HybridStore(SQLite FTS5 BM25 + float32 向量 + RRF 融合)。**零新增硬依赖**（ChromaDB/LanceDB 方案被否，改用 SQLite 内置 FTS5 + struct 打包，符合「核心依赖 4 个」原则）。numpy 可选加速。中文 2-gram 分词处理 CJK 检索。30 测试
- **接线**: `renyao/skills/rag` (4 工具: ingest/search/status/delete) + CLI `/rag` 命令组 + `config/rag.yaml`
- **注意**: 对话记忆 ✅ ≠ RAG ❌，两者互补

### P2-002 — 多模态输入
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-06
- **描述**: vision 工具集成 GLM-5V Turbo（智谱）图像分析；map_analyze 集成 SenseNova-SI-1.3（商汤）空间智能。超预期完成

### P2-003 — 语音输入
- **状态**: ⬜ 待办
- **创建**: 2026-07-28
- **描述**: 本地语音转文字（Whisper.cpp / faster-whisper），终端内语音输入。对标 OpenClaw Voice Wake
- **预计**: 2-3 天

### P2-004 — 后台常驻 Agent
- **状态**: 🔵 进行中（调度器已有，缺系统托盘）
- **创建**: 2026-07-28 | **更新**: 2026-08-10
- **描述**: `tianyao/agent_scheduler.py` 已实现 TimeScale 自主 tick（PUSH/PULL/THRESHOLD 三种同步模式）。待完成：系统托盘图标 + 后台轮询 + 定时自主巡检
- **预计**: 3-5 天

### P2-005 — 多 Agent 编排
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-06
- **描述**: `renyao/orchestrator.py` (490 行) — create_agent(worker 隔离) → dispatch(依赖等待) → execute_parallel(asyncio.gather) → verify(对抗性多 Agent 验证投票) → destroy(清理)。CLI `/orchestrate` + Server `/agents/*` API 均已接线

### P2-006 — Agent 间通信协议
- **状态**: ⬜ 待办
- **创建**: 2026-07-28 | **更新**: 2026-08-10
- **描述**: Agent 间直接消息传递、共享记忆、辩论/投票机制。当前子 Agent 仅能通过编排器中转，无直接通信。三爻消息协议（`sdk/trigram.py`）已定义 TrigramsMessage 基础设施
- **预计**: 5-7 天
- **OpenClaw 对标**: HiClaw (Matrix 协议)

---

## 五、P3 — 系统级集成（对标 OpenClaw OS 层）

### P3-001 — 系统控制
- **状态**: ⬜ 待办
- **创建**: 2026-07-28
- **描述**: 剪贴板读写、系统通知、窗口管理、音量/亮度控制
- **预计**: 3-5 天

### P3-002 — 沙箱安全
- **状态**: ✅ 已完成（执行隔离层）
- **创建**: 2026-07-28 | **完成**: 2026-08-06
- **描述**: `diyao/sandbox.py` (221 行) — DockerSandbox(`--network none --memory 256m --cpus 1 --read-only --tmpfs`) + LocalSandbox 跨平台降级。已在 shell skill 中使用。9 个测试
- **待补充**: 文件系统访问控制白名单、网络策略层 (P3 延伸)

### P3-003 — 移动端适配
- **状态**: 🔵 进行中
- **创建**: 2026-07-28 | **更新**: 2026-08-10
- **描述**: `F:\tianshu_dev\android\` — Kotlin/Gradle 项目：原生登录页 + WebView 加载聊天页。服务器端 `/login` API + token 鉴权已完成。待跑通完整链路
- **预计**: 3-5 天

### P3-004 — 文档站
- **状态**: ⬜ 待办
- **创建**: 2026-07-28
- **描述**: MkDocs + 中英文文档 + API 参考 + 教程。对标 OpenClaw 文档
- **预计**: 2-3 天

### P3-005 — 性能优化
- **状态**: ⬜ 待办
- **创建**: 2026-07-28
- **描述**: 启动速度 < 1s、首次 token 延迟优化、Prompt Cache 命中率监控
- **预计**: 3-5 天

### P3-006 — 监控与可观测性
- **状态**: ✅ 已完成
- **创建**: 2026-07-28 | **完成**: 2026-08-06
- **描述**: `/cost` Token 消耗统计、`/status` 系统状态、`/audit` 审计查询、工具调用 spinner + 计时 + 永久记录、错误面板(Rich Panel 红框)

### P3-007 — Skills 市场
- **状态**: ⬜ 待办
- **创建**: 2026-07-28
- **描述**: 社区上传/下载/评分 Skill、版本管理、依赖声明。对标 OpenClaw SkillHub
- **预计**: 7-10 天

---

## 六、Android 端 (独立仓库)

### AND-001 — Android APP 跑通完整链路
- **状态**: 🔵 进行中
- **创建**: 2026-08-06 | **更新**: 2026-08-10
- **位置**: `F:\tianshu_dev\android\` (gitignored，不在主仓库)
- **当前**: Kotlin/Gradle KTS · LoginActivity(原生登录+token) · MainActivity(WebView→chat.html) · 本地 HTML 回退页
- **阻塞**: 硬编码客户端凭据(`zhuzhe/123456`)需替换为服务器 `/login` 流程；需验证 WebView→服务器完整链路
- **预计**: 2-3 天

---

## 七、路线图外新增（2026-07-28 → 08-12）

以下功能在 roadmap 编写后新增，已全部完成：

| 功能 | 说明 | 完成日 |
|------|------|:------:|
| 地图/空间/路径 | `gateway/map.html` + SenseNova + OSMnx | 08-06 |
| Landing/部署/Nginx | `landing.html` + deploy.ps1 + nginx-yaopole.conf | 08-06 |
| 登录鉴权 + Skills | TIANSHU_LOGIN_PASSWORD + 16 skills | 08-06 |
| MCP Client | stdio + HTTP, McpClientManager, /mcp 命令 | 08-10 |
| 硬件控制 v0.3 | WorldAdapter ABC, Modbus/Voxel/Sim 三后端 | 08-11 |
| 社交媒体 | 5 平台 MCP Server, CDP JS 渲染 | 08-11 |
| 统一世界 v0.1 | 一个 Server 多种后端 | 08-11 |
| 体素世界 | Craft Python 3 port + VoxelAdapter | 08-11 |
| 路由修复 | _parse_pref bug fix, v4-pro 正确路由 | 08-11 |
| 安全加固 | run() 加入三道闸门 | 08-11 |
| SSE 真流式 | token 级别实时输出 | 08-11 |
| 死代码清理 | 删除 5 个死模块 ~800 行 | 08-11 |
| Soul.md 帮助 | 18 个命令自助文档 + MCP 工具提示 | 08-12 |
| CLI 工具图标 | 搜索/文件/Shell/MCP 分类图标 | 08-12 |
| MCP 状态显示 | 启动时显示 server 连接状态 | 08-12 |
| MCP 工具注入 | 动态工具列表注入 system prompt | 08-12 |
| Agent 评测框架 | YAML 场景 + mock LLM 评分 | 08-12 |
| PDF 工具箱 | Edge headless print-to-pdf + pypdf (13 工具: 生成/合并/拆分/旋转/水印/加密/表单) | 08-13 |

---

## 八、进度统计

| 优先级 | 总数 | 已完成 | 进行中 | 待办 |
|:------:|:----:|:------:|:------:|:----:|
| P0 | 5 | 3 | 0 | 2 |
| P1 | 7 | 5 | 0 | 2 |
| P2 | 6 | 3 | 1 | 2 |
| P3 | 7 | 2 | 1 | 4 |
| Android | 1 | 0 | 1 | 0 |
| **合计** | **26** | **13** | **3** | **10** |

**完成率: 50%** (13/26)，另有 3 项进行中

---

## 九、建议执行顺序（更新）

```
Week 1-2:   P0-001(pip包·改名发布) → P0-002(死代码清理)
Week 3-4:   P1-004(MCP协议) → P1-005(Playwright完整集成) → P2-001(RAG)
Week 5-6:   P1-001(飞书Bot) → AND-001(Android跑通) → P2-003(语音)
Week 7-8:   P2-006(Agent间通信) → P2-004(系统托盘) → P3-003(移动端完成)
Week 9-12:  P3-001(系统控制) → P3-004(文档站) → P3-005(性能) → P3-007(Skills市场)
```

---

> 最后更新: 2026-08-12
> 下次复查: 每完成一项后更新状态 + 时间戳
