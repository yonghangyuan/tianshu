# 天枢 V2 架构设计

> 2026-07-30 | 博采 Hermes + OpenClaw 之长，守三爻之根本

---

## 零、天枢的独有基因（Hermes 和 OpenClaw 都没有的）

| 基因 | 含义 | 不可动摇 |
|------|------|:---:|
| **三爻架构** | 天=规律/审计 · 人=目的/决策 · 地=物质/执行 | ✅ |
| **天曜审计** | 4 级决策追溯（BASIC→SNAPSHOT→FULL→EVALUATED） | ✅ |
| **模型自主** | 不依赖境外 API，用户完全控制模型选择 | ✅ |
| **数据主权** | 本地存储，零遥测，不跨境 | ✅ |
| **自有浏览器** | Edge CDP，零外部下载 | ✅ |
| **自有搜索** | cn.bing/搜狗直连，零 API key | ✅ |

---

## 一、从 Hermes 学的 → 天枢自己的做法

### Hermes: SKILL.md 格式

Hermes 的技能是 Markdown 文件。天枢的 Skills 目前是 Python 类。

**天枢 V2 方案**: **人曜 Skill 体系**
```
skills/<category>/<name>/
├── SKILL.md          ← 技能描述（Markdown，LLM 可读）
├── handler.py        ← 执行逻辑（Python，可选）
└── tools.json        ← 工具声明（name/params/permission）
```

与人曜的"目的性"对应——每个 Skill 声明**为什么**存在、**解决什么**问题。

Hermes 有但天枢改进的：
- Hermes: skill_manage 工具 → 天枢: **人曜 /learn 命令**，由用户主动触发，不自动生成
- Hermes: Curator 自动审核 → 天枢: **天曜审计** 跟踪技能质量，但不自动删改

### Hermes: Context Compressor

Hermes 用辅助模型压缩上下文。

**天枢 V2 方案**: **天爻上下文管理**

不是简单压缩——是**审计驱动的上下文管理**：
1. 每轮对话生成摘要（用 DS v4-flash 便宜模型）
2. 摘要标记"已解决/待处理"
3. 天曜记录压缩决策（什么被压缩、为什么、压缩后 token 数）
4. 用户可回查被压缩前的完整对话

Hermes 的压缩是黑盒——天枢的压缩是可审计的。

### Hermes: Memory System

Hermes 有三层记忆（working/episodic/procedural）+ Honcho 心理画像。

**天枢 V2 方案**: **人爻记忆体系**

天枢已有 MEMORY.md + USER.md + SQLite。保持三层但重新定义：

| 层 | 存储 | 用途 | 对应 |
|---|---|---|---|
| **天**（规律） | 审计记录 | 决策可追溯 | 天曜 |
| **人**（目的） | USER.md + 画像 | 理解用户意图 | 人曜 |
| **地**（物质） | MEMORY.md + SQLite | 事实、偏好、知识 | 地曜 |

---

## 二、从 OpenClaw 学的 → 天枢自己的做法

### OpenClaw: Plugin SDK

**天枢 V2 方案**: **三爻技能 SDK**

每个 Skill 必须声明三爻属性：
```yaml
# SKILL.md frontmatter
trigram: 地          # 天/人/地
permission: READ     # SAFE/READ/WRITE/DANGER
audit: true          # 是否纳入天曜审计
```

OpenClaw 的 Plugin SDK 是纯技术抽象——天枢的 Skill SDK 是哲学驱动的。

### OpenClaw: Gateway 控制平面

**天枢 V2 方案**: **天枢 Gateway——枢纽**

```
Gateway（枢纽）
├── 路由层      ← 消息从哪来、到哪去
├── 会话层      ← 哪个 session 用哪个 model
├── 审计层      ← 天曜：所有流经 Gateway 的决策都记录
├── 技能层      ← 人曜：根据 task 匹配 Skill
└── 执行层      ← 地曜：调度 Provider 执行
```

OpenClaw 的 Gateway 是技术中心——天枢的 Gateway 是**天·人·地**三爻的交汇点。

### OpenClaw: Tool Registry

**天枢 V2 方案**: **按模式 + 按三爻过滤**

```
normal 模式 → 全工具
plan 模式   → 只暴露 天爻工具（审计/搜索/阅读）
auto 模式   → 全工具，跳过确认
```

OpenClaw 过滤的是 capability——天枢过滤的是**三爻归属 + 安全级别**。

---

## 三、天枢独有的创新（Hermes 和 OpenClaw 都没有）

### 3.1 天曜决策链

```
用户输入 → 路由决策(为什么选这个模型) → 工具调用(为什么选这个工具)
→ 结果评估(是否符合预期) → 全部记录，可追溯
```

这不是 Hermes 的 conversation history，不是 OpenClaw 的 session transcript——是**因果链**。

### 3.2 三爻会话视图

同一个会话，三种视角：
- **天爻视图**: 审计记录、决策时间线
- **人爻视图**: 用户意图、偏好变化、USER.md 演化
- **地爻视图**: 文件读写、Shell 执行、网络请求

### 3.3 模型自主路由

不是"哪个模型最好"——是"用户选择了哪个模型"。路由规则完全由 `providers.yaml` 控制，框架不预设。

---

## 四、不做的（从两个框架中学到的教训）

| 不做 | 原因 |
|---|---|
| 自动技能生成 | Hermes Curator 自动删改技能导致不可控 |
| 全球多通道 | OpenClaw 23 个平台涉及数据出境 |
| 云部署模板 | 数据主权，必须本地部署 |
| 自动模型切换 | 用户控制模型选择，不是框架 |
| 封闭生态 | 不强制用某一家的 API/服务 |

---

## 五、V2 路线图

```
Phase A: 三爻 Skill SDK（1 周）
  ├─ SKILL.md 标准格式 + tools.json 声明
  ├─ ToolRegistry 集中注册 + 模式过滤
  └─ /learn 命令（用户触发，非自动）

Phase B: 天枢 Gateway（1-2 周）
  ├─ 统一 HTTP + WebSocket 入口
  ├─ 天曜全链路审计
  └─ 飞书 Bot 跑通（第一个真正可用的通道）

Phase C: 天爻上下文 + 人爻记忆（1 周）
  ├─ 审计驱动的上下文压缩
  └─ 三层记忆体系打通

Phase D: 打磨 + 测试（持续）
  ├─ 端到端集成测试
  └─ 文档站
```

---

> 天枢不是 Hermes + OpenClaw 的拼装。
> 天枢是北斗第一星——有自己的轨道，只是借别人的光看清前面的路。
