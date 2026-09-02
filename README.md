# 天枢 Tianshu

```
  ████████╗ ██╗ █████╗ ███╗  ██╗  ███████╗██╗  ██╗██╗  ██╗    · 摇光
  ╚══██╔══╝██╔══██╔══██╗████╗ ██║  ██╔════╝██║  ██╗██╗  ██╗
     ██║  ██║ ███████║██╔██╗██║  ███████╗███████║██║  ██║      · 开阳
     ██║  ██║ ██╔══██║██║╚████║  ╚════██║██╔══██║██║  ██║
     ██║  ██║ ██╔══██║██║ ╚███║  ███████║██║  ██║╚██████╔╝        · 玉衡
     ╚═╝  ╚═╝ ╚═╝  ╚═╝╚═╝  ╚══╝  ╚══════╝╚═╝  ╚═╝ ╚═════╝
                                                         · 天权          · 天玑

                                                                  · 天璇

                                                                   ★ 天枢
```
> 北斗七星第一星 · 主司枢纽与导向 · 中国本土自主 AI Agent 框架

[![Test](https://github.com/yonghangyuan/tianshu/actions/workflows/test.yml/badge.svg)](https://github.com/yonghangyuan/tianshu/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/tianshu-agent)](https://pypi.org/project/tianshu-agent/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

天枢是一个对标 Claude Code 的终端 AI Agent 框架，专注中国本土模型生态（DeepSeek / 豆包 / 智谱 / Moonshot / Ollama 本地），强调**模型自主、数据主权、决策可追溯**。

---
<img width="1025" height="1434" alt="image" src="https://github.com/user-attachments/assets/58fc11c1-8af6-473d-bebe-1159f61bb22f" />


## 安装

```bash
pip install tianshu-agent        # import 名为 tianshu

# 配置 API Key（至少一个）
export DEEPSEEK_API_KEY="sk-..."

# 启动
tianshu-cli
```

或从源码：`git clone` + `pip install -e ".[all]"`

---

## 架构

```
tianshu/
├── core/       # AgentCore — ReAct 循环 + 路由 + 策略引擎 + 预设系统
├── diyao/      # 地曜 — Provider 层 (DS/豆包/智谱/Moonshot/Ollama) + 沙箱
├── renyao/     # 人曜 — 15 Skills + MCP Client + 星群通信 + 编排器
├── tianyao/    # 天曜 — 4 级审计 + Cron 调度
├── memory/     # 长期记忆 (FTS5 + Digest + Decay) + 用户画像 + 会话持久化
├── rag/        # RAG 知识库 — BM25 + 向量混合检索 (SQLite，零重依赖)
├── gateway/    # CLI / TUI / HTTP Server / Android / 飞书 / 微信 / QQ
└── sdk/        # 统一数据模型
```

**三爻设计哲学**：☰ 天爻（规律/审计）· ☱ 人爻（目的/决策）· ☷ 地爻（物质/执行）——所有工具调用必经权限 + 策略 + 确认三道闸门，审计六问全程留痕。

---

## 特性

**Agent 内核**
- 🚀 **SSE token 级流式** + Rich Markdown 渲染 + 底部状态栏（模式/预设/Token/缓存命中率常驻）
- 🎚️ **双轴模式** — 执行模式（normal/auto/plan）× 预设（standard/minimal/code）正交组合；极简模式=持久 shell + 行级编辑免确认；代码模式(PTC)=模型写 Python 组合工具，中间结果不占上下文
- 🛡️ **三道闸门** — 权限四级(SAFE/READ/WRITE/DANGER) + 声明式策略引擎 + 用户确认；`run()`/`run_stream()` 均不可绕过
- 🔍 **模型路由** — 按任务类型自动选模型，F4 菜单会话级切换；Ollama 本地模型零代码接入
- 💾 **记忆系统** — SQLite FTS5 全文检索 + 自动画像 + Digest/Decay/Compress，对话式记忆注入
- 📊 **审计六问** — 每次决策留痕"谁、有什么信息、考虑了什么、什么约束、结果如何、能否复现"

**生态能力**
- 📚 **RAG 知识库** — BM25 + 向量混合检索（RRF 融合），PDF/Markdown/代码摄取，中文 2-gram
- 🔌 **MCP Client** — stdio + Streamable HTTP 双 transport，多 server 生命周期管理
- 🌌 **星群通信** — StarBus 总线：Agent 间点对点消息 / 话题广播 / 共享记忆板 + 辩论 / 投票；多 Agent 编排（并行执行 + 对抗性验证）
- 🌐 **零依赖搜索/浏览** — cn.bing→搜狗→百度三层 fallback；httpx + Edge CDP 渲染 JS 页面，无需 API Key 与外部浏览器下载
- 📄 **PDF 工具箱** — Edge headless 渲染（CJK 字体）+ pypdf 13 工具（生成/合并/拆分/水印/加密/表单）
- 🖼️ **多模态** — GLM 图像理解 + 空间智能分析

**产品矩阵**
- 📱 **手机助手**（TS-018）— 全内置 APK：Chaquopy 嵌 Python Agent，无障碍服务感知+操作手机，对话即控制；前台服务进度通知 / 通知栏确认 / 高危应用 deny / 动作审计
- 💬 **HTTP Server** — 星群群聊 UI（SSE 流式 + @补全），40+ 端点，Bearer/Cookie 鉴权

---

## 命令

| 命令 | 说明 |
|------|------|
| `/help` | 帮助 |
| `/models` `/model` | 模型列表 / 切换（F4 菜单） |
| `/mode` | 循环 normal → auto → plan（Shift+Tab） |
| `/preset` | 预设切换 standard → minimal → code（F2） |
| `/rag ingest/search` | RAG 知识库摄取 / 检索 |
| `/mcp` | MCP server 管理与工具列表 |
| `/star` | 星群通信（消息/辩论/投票） |
| `/session list/resume` | 会话管理 |
| `/audit` `/cost` | 审计记录 / Token 与缓存命中统计 |
| `/skills` `/cron` | Skills / 定时任务 |

---

## 测试

```bash
pytest tests/ -q    # 337 个用例，CI 覆盖 Ubuntu + Windows × Python 3.11-3.13
```

---

## License

MIT
