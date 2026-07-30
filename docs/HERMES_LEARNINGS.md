# Hermes Agent 架构分析 + 天枢改进计划

> 分析日期: 2026-07-30 | 基于 Hermes Agent main 分支源码

---

## 一、Hermes 架构总览

```
hermes-agent/
├── agent/               ← 核心引擎（30+ 文件，~50K 行）
│   ├── conversation_loop.py   (7034行) 主循环
│   ├── context_compressor.py  (5619行) 上下文压缩
│   ├── tool_executor.py       (2060行) 工具执行
│   ├── curator.py             (2018行) 技能自进化审核
│   ├── error_classifier.py    (1790行) 错误分类
│   ├── turn_context.py        (1262行) 回合上下文管理
│   ├── memory_manager.py      (1241行) 记忆管理
│   ├── learn_prompt.py        (150行)  /learn 技能生成
│   └── ...
├── tools/               ← 100+ 工具文件
│   ├── file_tools.py, browser_tool.py, web_tools.py
│   ├── mcp_tool.py, cronjob_tools.py, terminal_tool.py
│   └── registry.py — 统一注册中心
├── skills/              ← 14 个技能分类目录
│   └── 每个技能 = SKILL.md (Markdown + YAML frontmatter)
├── gateway/             ← 23 个消息平台适配器
│   └── platforms/{weixin,whatsapp,signal,telegram...}.py
├── plugins/             ← 20 个插件（browser/memory/kanban/spotify...）
├── providers/           ← 模型适配器
└── tests/               ← 1428 个测试
```

### 核心设计哲学

1. **Skills 是 Markdown 文件，不是 Python 代码**——技能是 SKILL.md，用 YAML frontmatter 描述，LLM 自己读、自己理解、自己执行
2. **/learn 不是引擎，是 prompt**——技能生成就是一段精心设计的 prompt，让 LLM 用自己的工具去收集信息、写出 SKILL.md
3. **所有工具统一注册**——`tools/registry.py` 是唯一的工具入口，任何工具加个文件+注册即可
4. **平台适配器模式**——所有消息平台继承 `base.py`，只需实现 send/receive

---

## 二、天枢当前架构 vs Hermes

| 模块 | 天枢 | Hermes | 差距 |
|------|------|--------|:----:|
| Agent Loop | `service.py` 200行 | `conversation_loop.py` 7034行 | 35x |
| 上下文管理 | `_build_messages()` 简单截断 | `context_compressor.py` 5619行 | 无压缩 |
| 工具系统 | 9 个 Python Skill 类 | 100+ 文件 + registry | 碎片化 |
| 技能格式 | Python 代码 | SKILL.md (Markdown) | 不兼容 |
| 技能生成 | `observer.py` 每5轮调LLM | `/learn` prompt 模板 | 不可控 |
| 记忆 | SQLite 键值对 | MemoryManager + provider 模式 | 单薄 |
| 多通道 | 6 个骨架 | 23 个平台适配器 | 未跑通 |
| 测试 | 53 单元 | 1428 测试 | 27x |
| Token 预算 | ✅ 刚加 | ✅ | 持平 |
| 错误分类 | ✅ 刚加 | ✅ 1790行 | 基础版 |

---

## 三、值得借鉴的设计（按性价比排序）

### 🔴 P0 — 立即做（半天，高收益）

#### 1. Skills 改为 Markdown 格式
**Hermes 做法**: 每个技能是 `SKILL.md`，放在 `skills/<category>/<name>/SKILL.md`
```yaml
---
name: browser
description: Browse web pages and extract text.
version: 0.1.0
author: Hermes
metadata:
  hermes:
    tags: [Web, Browser]
---
# Browser Skill
...
```

**天枢现状**: 技能是 Python 类 (`file_ops.py`, `browser.py`...)，改一个参数要改代码

**改进方案**: 保留 Python handler，但技能描述改为 SKILL.md 格式。让 LLM 自己读技能定义，而不依赖硬编码的 tool schema。

#### 2. /learn 命令 — LLM 自己生成技能
**Hermes 做法**: `/learn` 只是一段 prompt（`learn_prompt.py` 150行）。用户说"把刚才的操作变成技能"，LLM 用自己的工具收集上下文→写 SKILL.md→保存。

**天枢现状**: `observer.py` 自动检测模式生成技能，但质量不可控

**改进方案**: 加 `/learn <描述>` 命令。不是自动生成——是用户主动触发。LLM 用已有的 browse/read_file 工具收集信息，写出 SKILL.md。

### 🟡 P1 — 本周做（1-2天，中等收益）

#### 3. 上下文压缩升级
**Hermes 做法**: 用辅助模型（便宜模型）总结中间轮次，保护头尾。结构化摘要格式（Resolved/Pending）。Token 预算保护尾部。

**天枢现状**: `_build_messages()` 简单截断——头（system）+ 尾（最近4条），中间全丢

**改进方案**: 
- 中间消息先做轻量文本摘要（用 DS v4-flash 便宜模型）
- 摘要格式：「已完成: X / 待处理: Y」
- Token 预算驱动何时压缩

#### 4. 工具注册中心统一化
**Hermes 做法**: `tools/registry.py` 统一注册。每个工具有 name/description/parameters/permission。

**天枢现状**: 工具散落在 Skill 类的 `get_tools()` 方法里，没有统一视图

**改进方案**: 建 `tools/registry.py`，启动时扫描所有 Skill→收集工具→统一注册。给 `/tools` 命令用。

### 🟢 P2 — 后续考虑（3-5天，长期收益）

#### 5. 记忆系统升级
**Hermes 做法**: MemoryManager + provider 模式。支持多后端（内置/Honcho/Mem0）。Pre-turn prefetch + post-turn sync。

**改进方案**: 保持现有 SQLite 方案，加 provider 接口支持扩展。

#### 6. 消息平台对接
**Hermes 做法**: `gateway/platforms/` 统一 base.py，每个平台只需实现 send/receive

**改进方案**: `gateway/` 下已有飞书/微信/QQ 骨架，按 base.py 模式重构

---

## 四、执行计划

```
Day 1: P0-1 Skills Markdown化 + P0-2 /learn 命令
Day 2-3: P1-3 上下文压缩升级 + P1-4 工具注册中心
Week 2+: P2 记忆系统 + 消息平台
```

---

> 关键原则：学 Hermes 的**设计思路**，不抄 Hermes 的**代码量**。
> Hermes 7034 行的 conversation_loop 是因为处理了 200+ 种异常情况。
> 天枢只需要处理自己场景里的异常——不是越重越好。
