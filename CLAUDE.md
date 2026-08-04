# 天枢 Tianshu · 项目说明书

> 最后更新: 2026-08-03

---

## 一、项目定位

中国本土自主 AI Agent 框架。对标 Claude Code，借鉴 Hermes Agent + OpenClaw，但走自己的路。

**核心原则**: 独立自主 · 模型自主 · 数据主权 · 可审计 · 可追溯 · 可控制

---

## 二、架构

```
src/tianshu/
├── core/       AgentCore · Router · Planner · PolicyEngine · ToolRegistry · Commands
├── diyao/      地曜 — Provider 层 (DS/豆包/智谱/Moonshot) · Sandbox
├── renyao/     人曜 — 12 Skills · 插件 · 自进化
│   └── skills/ browser · web_search · file_ops · intel · shell · paper_radar ...
├── tianyao/    天曜 — 4 级审计 · Cron 调度
├── memory/     L2(MEMORY.md/USER.md) + L5(SQLite FTS5) · Prefetch · Digest · Decay · Compress
├── gateway/    CLI · TUI · Server(群聊) · 飞书/微信/QQ(骨架)
└── sdk/        统一数据模型
```

65 文件 · ~12K 行 · 53 测试 · 4 核心依赖(httpx/pyyaml/aiosqlite/rich)

---

## 三、关键设计决策

1. **三爻架构**: 天(治理/仲裁)·人(决策/调度)·地(感知/执行)——三道不可绕过的闸门。接口规范见 `docs/TIANSHU_INTERFACE_SPEC.md`
2. **信息·能量·物质**: 天枢不是 Agent 框架，是三条流的调度系统。城市大脑和工业物联网是同一套架构在不同场景下的实例化
3. **时间异质性**: 每条消息有 TTL，每个 Agent 有时间尺度，信息按指数衰减——现有所有 Agent 框架缺失的维度
4. **审计六问**: 决策前就写清"谁、有什么信息、考虑了什么、什么约束、结果如何、能复现吗"
5. **自有浏览器**: httpx快速抓取 + Edge CDP渲染JS页面。零外部下载
6. **自有搜索**: cn.bing.com → 搜狗 → 百度。零 API Key
7. **代码量控制**: 12K行·一人可全审。不追功能数量，追可信度

---

## 四、当前能力

- CLI: tianshu-cli (Rich渲染·流式输出·模式切换·@file引用)
- Server: tianshu-server (群聊·文件分享·Markdown渲染·@自动补全)
- TUI: tianshu (Rich TUI·热键切换)
- 25 工具·12 Skills·4 Provider·10 模型
- Token 预算 500K·重复调用检测·错误分类
- PolicyEngine: 6条声明式策略·工具执行前拦截
- 记忆: 5功能(prefetch/digest/decay/compress/boost)
- Planner: Plan Mode 下 JSON 计划→逐步执行
- 登录系统: 代码已写·服务器未部署
- Android APP: WebView骨架·登录页·未完成

---

## 五、待完成

**P0（本周）** ✅ 已完成 2026-08-04
- [x] pip 包规范：__version__ 统一 0.2.0，requirements.txt 同步
- [x] 模块导出：6 个 __init__.py 补齐 __all__
- [x] 配置热加载：CLI /reload + Server POST /admin/reload
- [x] 错误友好化：API/网络/鉴权/限流 4 类中文提示
- [x] CI 完善：pip install -e .[all] + pytest --tb=short

**剩余 P0**
- [ ] Android APP 跑通(WebView加载聊天页)
- [ ] 服务器部署登录鉴权
- [ ] E2E 集成测试(至少1条: 搜索→浏览→总结)

**P1 (下周)**
- [ ] 星群多Agent编排(多实例同群协作)
- [ ] 上下文语义压缩(辅助模型总结·非简单截断)

**P2 (后续)**
- [ ] 飞书 Bot 跑通
- [ ] pip 包发布
- [ ] Mac/Linux 兼容
- [ ] Plan Mode 空错误修复
- [ ] RAG 私有知识库

---

## 六、部署状态

| 环境 | 地址 | 状态 |
|---|---|---|
| 本地 Windows | localhost | 开发机 |
| 腾讯云 Ubuntu | 175.27.157.139:8720 | 运行中(无鉴权) |
| GitHub | github.com/yonghangyuan/tianshu | 25+ commits |

服务器更新方式: scp 文件 → 重启进程(GitHub被墙)

---

## 七、常用命令

```bash
# 本地
tianshu-cli                  # CLI
tianshu-server --port 8720   # Server
pytest tests/ -q             # 测试

# 服务器
ssh ubuntu@175.27.157.139
cd ~/tianshu && source .venv/bin/activate
tianshu-server --port 8720

# 部署(scp)
scp F:\tianshu\src\tianshu\gateway\server.py ubuntu@175.27.157.139:~/tianshu/src/tianshu/gateway/
```

---

## 八、关键文件

| 文件 | 内容 |
|---|---|
| `core/service.py` | AgentCore.run_stream() —— 主循环 |
| `core/turn_machine.py` | 状态机重构(参考·未接入) |
| `core/policy_engine.py` | 策略引擎 |
| `core/planner.py` | 任务规划器 |
| `core/tool_registry.py` | 工具注册中心 |
| `memory/service.py` | 记忆系统 |
| `gateway/server.py` | HTTP Server + 群聊 |
| `gateway/cli.py` | Rich 渲染器 |
| `gateway/chat.html` | 群聊前端 |
| `config/soul.md` | 系统提示词 |
| `config/policy.yaml` | 策略规则 |
| `config/providers.yaml` | 模型配置 |
| `docs/ROADMAP.md` | 25项路线图 |
| `docs/TIANSHU_ARCHITECTURE_V2.md` | V2架构设计 |

---

## 九、本地开发文件

`F:\tianshu_dev\` — 不推送的本地文件:
- `docs/PHILOSOPHY.md` — 哲学基础
- `docs/VISION.md` — 愿景
- `docs/constellation_design.md` — 星群设计
- `docs/intelligence_system_design.md` — 情报系统设计
- `docs/android_app_plan.md` — Android计划
- `android/` — Android项目源码

`F:\hermes\多智能体信息协同\` — 相关文档:
- `天枢_哲学基础_20260731.docx` / `天枢_哲学基础_v2.docx`
- `天枢开发总结_2026-07-31.md`

---

## 十、学习参考

- **Hermes Agent** (Nous Research): 自进化·Curator·SKILL.md格式
- **OpenClaw**: Gateway控制平面·Plugin SDK·多通道
- **王戟教授**: 高可信软件架构理论→天曜审计

天枢不是 Hermes + OpenClaw 的拼装。天枢有自己的轨道。
