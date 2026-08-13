# 天枢 Tianshu · 项目说明书

> 最后更新: 2026-08-11

---

## 一、项目定位

中国本土自主 AI Agent 框架。三爻架构(天/人/地)·MCP 协议·多 Agent 编排。

**核心原则**: 独立自主 · 模型自主 · 数据主权 · 可审计 · 可追溯 · 可控制

---

## 二、架构

```
src/tianshu/
├── core/       AgentCore · Router · Planner · PolicyEngine · ToolRegistry · Commands
├── diyao/      地曜 — Provider 层 (DS/豆包/智谱/Moonshot) · Sandbox
├── renyao/     人曜 — 15 Skills · MCP Client · 插件 · 自进化
│   └── skills/ browser · web_search · file_ops · intel · shell · paper_radar · rag · pdf_export ...
├── tianyao/    天曜 — 4 级审计 · Cron 调度
├── memory/     L2(MEMORY.md/USER.md) + L5(SQLite FTS5) · Prefetch · Digest · Decay · Compress
├── rag/        RAG 知识库 — chunker · embedder(API/Mock) · HybridStore · RAGService
├── gateway/    CLI · TUI · Server(星群群聊) · 飞书/微信/QQ(骨架)
└── sdk/        统一数据模型
```

62 文件 · ~18K 行 · 164 测试 · 核心依赖(httpx/pyyaml/aiosqlite/rich)

---

## 三、关键设计决策

1. **三爻架构**: 天(治理/仲裁)·人(决策/调度)·地(感知/执行)——三道不可绕过的闸门
2. **MCP 协议**: 完整 MCP Client 实现，支持 stdio + HTTP，已集成 4 个外部 server
3. **自有浏览器**: httpx快速抓取 + Edge CDP渲染JS页面。零外部下载
4. **自有搜索**: cn.bing.com → 搜狗 → 百度。零 API Key
5. **安全**: run() 和 run_stream() 均有权限+策略+确认三道闸门
6. **审计六问**: 决策前就写清"谁、有什么信息、考虑了什么、什么约束、结果如何、能复现吗"
7. **模型路由**: 自动根据任务类型选择模型(routing rules)，支持用户直接指定

---

## 四、当前能力

- CLI: tianshu-cli (Rich渲染·流式输出·模式切换·@file引用·Spinner工具跟踪)
- Server: tianshu-server (星群群聊·SSE流式·Markdown渲染·@自动补全)
- 46 工具·15 Skills·4 Provider·10 模型
- PDF 工具箱: Markdown/HTML → PDF (Edge headless 渲染·CJK 系统字体) + 合并/拆分/旋转/水印/加密/提取/表单 (pypdf)，对标 Hermes pdf skill
- PolicyEngine: 6条声明式策略·工具执行前拦截
- Planner: Plan Mode 下 JSON 计划→逐步执行
- 记忆: FTS5全文检索 + 自动画像 + Digest + Decay + Compress，对标 Honcho/Mem0
- RAG 知识库: SQLite FTS5(BM25) + float32 向量混合检索(RRF融合)，零新增硬依赖，PDF/Markdown/代码摄取
- MCP Client: 完整 stdio + HTTP 双 transport，支持多 server 并行
- WorldAdapter: 统一 Modbus/Voxel/Sim 后端接口
- 真 SSE 流式: token 级别实时输出

---

## 五、已集成的外部 MCP Server

| Server | 协议 | 用途 |
|--------|------|------|
| tianshu-hardware | HTTP | 工业 Modbus 设备控制 (79 tests) |
| tianshu-world | HTTP | 统一世界服务器 (3 种后端) |
| tianshu-social | HTTP | 社交媒体搜索引擎 (5 平台) |
| filesystem | stdio | 本地文件操作 |

---

## 六、待完成

**P0**
- [ ] Android APP 跑通

**P1**
- [ ] 星群 Agent 间直接通信协议
- [ ] pip 包发布（需改名，`tianshu` 已被占）
- [x] RAG 私有知识库 (08-13)

**P2**
- [ ] 飞书 Bot 跑通
- [ ] Mac/Linux 全面兼容
- [ ] Playwright 集成（截图/点击/填表）
- [ ] Skills 市场

---

## 七、部署状态

| 环境 | 地址 | 状态 |
|---|---|---|
| 本地 Windows | localhost | 开发机 |
| 腾讯云 Ubuntu | 175.27.157.139:8720 | 运行中 |
| GitHub | github.com/yonghangyuan/tianshu | 已同步 |
| Gitee | gitee.com/jiojio21/tianshu | 已同步 |

---

## 八、常用命令

```bash
tianshu-cli                  # CLI
tianshu-server --port 8720   # Server
pytest tests/ -q             # 测试 (133 passed)
tianshu-world --backend sim  # 统一世界服务器
tianshu-social --port 8750   # 社交媒体搜索
```

CLI 内: `/rag ingest <路径>` 摄取文档 · `/rag search <查询>` 混合检索 · `/rag` 查看状态

---

## 九、关键文件

| 文件 | 内容 |
|---|---|
| `core/service.py` | AgentCore.run_stream() —— 主循环 |
| `core/router.py` | 模型路由器 |
| `core/policy_engine.py` | 策略引擎 |
| `core/tool_registry.py` | 工具注册中心 |
| `renyao/mcp_client.py` | MCP 客户端 |
| `gateway/server.py` | HTTP Server + 星群 |
| `gateway/cli.py` | Rich 渲染器 |
| `config/soul.md` | 系统提示词(含操作帮助) |
| `config/mcp.yaml` | MCP Server 配置 |
| `config/providers.yaml` | 模型配置+路由规则 |
| `docs/ROADMAP.md` | 路线图 |
