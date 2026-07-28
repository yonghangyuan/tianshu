# 天枢 Tianshu

```
  ████████╗ ██╗ █████╗ ███╗  ██╗  ███████╗██╗  ██╗██╗  ██╗    · 摇光
  ╚══██╔══╝██╔╝██╔══██╗████╗ ██║  ██╔════╝██║  ██║██║  ██║
     ██║  ██║ ███████║██╔██╗██║  ███████╗███████║██║  ██║      · 开阳
     ██║  ██║ ██╔══██║██║╚████║  ╚════██║██╔══██║██║  ██║
     ██║  ██║ ██║  ██║██║ ╚███║  ███████║██║  ██║╚██████╔╝        · 玉衡
     ╚═╝  ╚═╝ ╚═╝  ╚═╝╚═╝  ╚══╝  ╚══════╝╚═╝  ╚═╝ ╚═════╝
                                                         · 天权          · 天玑

                                                                  · 天璇

                                                                   ★ 天枢
```
> 北斗七星第一星 · 主司枢纽与导向 · 中国本土自主 AI Agent 框架

[![Test](https://github.com/yonghangyuan/tianshu/actions/workflows/test.yml/badge.svg)](https://github.com/yonghangyuan/tianshu/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

天枢是一个对标 Claude Code 的终端 AI Agent 框架，专注中国本土模型生态（DeepSeek / 豆包 / 智谱 / Moonshot），强调**模型自主、数据主权、决策可追溯**。

---

## 架构

```
tianshu/
├── core/           # AgentCore — ReAct 循环 + 路由 + 审计
├── diyao/          # 地曜 — Provider 层 (DS/豆包/智谱/Moonshot)
├── renyao/         # 人曜 — Skill 系统 (9 个内置 + 自进化)
├── tianyao/        # 天曜 — 审计系统 + Cron 调度
├── memory/         # 长期记忆 + L4 用户画像 + 会话持久化
├── gateway/        # CLI / TUI / HTTP / 飞书 / 微信 / QQ
├── sdk/            # 统一数据模型
└── tests/          # 53 个单元测试
```

**三爻设计哲学**：☰ 天爻（规律/审计）· ☷ 人爻（目的/决策）· ☷ 地爻（物质/执行）

---

## 特性

- 🚀 **SSE 流式输出** — token 级实时渲染
- 🎨 **Rich CLI** — Markdown 渲染 + 代码语法高亮
- 📋 **文件操作 Skill** — `read_file` / `write_file` / `list_dir`，跨平台
- 🛡️ **权限系统** — SAFE/READ/WRITE/DANGER 四级，方向键确认面板
- 🔄 **模式切换** — Normal / Auto / Plan，Shift+Tab 切换
- 💾 **会话持久化** — SQLite 存储，支持恢复历史对话
- 🔍 **web_search** — DDG API → HTML 解析 → 手工链接，三层 fallback
- 🧬 **Skill 自进化** — 每 5 轮对话自动生成新 Skill
- 📊 **审计追踪** — 所有决策带唯一 ID，可回溯
- 🇨🇳 **多 Provider** — DeepSeek v4 / 豆包 / 智谱 GLM-4 / Moonshot

---

## 快速开始

```bash
# 安装
git clone https://github.com/yonghangyuan/tianshu.git
cd tianshu
pip install -e .

# 配置 API Key
export DEEPSEEK_API_KEY="sk-..."
export DOUBAO_API_KEY="..."

# 启动
tianshu-cli
```

---

## 命令

| 命令 | 说明 |
|------|------|
| `/help` | 帮助 |
| `/models` | 模型列表 |
| `/model <name>` | 切换模型 |
| `/mode` | 循环切换 normal → auto → plan |
| `/skills` | Skills 列表 |
| `/audit` | 审计记录 |
| `/memory` | 记忆状态 |
| `/session list/resume` | 会话管理 |
| `/cron list/add` | 定时任务 |
| `/clear` | 清屏 |

---

## 模式

```
⏵ normal    普通模式 — 工具操作需确认
⏵⏵ auto    自动模式 — 跳过所有确认
◉ plan      规划模式 — 只读分析
```

---

## 测试

```bash
pytest tests/ -v    # 53 个用例
```

---

## License

MIT
