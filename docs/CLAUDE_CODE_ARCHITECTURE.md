# Claude Code 全架构分析 — 天枢可借鉴的设计模式

> 基于作为 Claude Code 实例的深入使用体验 + 公开信息 + 源码推理
> 2026-08-05

---

## 一、整体架构

```
用户输入
  │
  ▼
┌─ Claude Code Client (Electron/Node) ──────────────────┐
│  · 终端 UI (Rich-style 渲染, 流式输出)                  │
│  · @file 引用解析 + 自动补全                             │
│  · 权限确认 UI (方向键 + 彩色面板)                       │
│  · 思考过程折叠/展开                                     │
│  · Diff 内联展示 (红绿行)                                │
│  · settings.json 配置                                    │
└─────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Core Agent Loop ─────────────────────────────────────┐
│  · ReAct Loop (与天枢相同)                              │
│  · 但增加了: Plan Mode 前置、Token 预算管理、            │
│    重复工具调用检测、错误分类+自动恢复、                  │
│    权限分级 (SAFE/READ/WRITE/DANGER)                    │
└─────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Tool System ─────────────────────────────────────────┐
│  · ~20 个工具, 每个窄而深                                │
│  · 统一注册 (类似天枢 ToolRegistry)                     │
│  · 文件操作: Read/Write/Edit/Glob/Grep                 │
│  · 终端: Bash (沙箱模式可选)                            │
│  · Web: WebSearch/WebFetch                             │
│  · Agent: Task/Agent/Workflow (子Agent编排)            │
│  · 记忆: 持久化 MEMORY.md                              │
└─────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Subagent System (Worktree Isolation) ────────────────┐
│  · 每个子Agent跑在独立 git worktree                     │
│  · 并行/串行编排 (pipeline/parallel)                    │
│  · 结果验证 (adversarial verify, completeness critic)  │
│  · 预算控制 (token budget per workflow)                 │
└─────────────────────────────────────────────────────────┘
```

**天枢对比：** 前三层基本对齐（CLI + AgentCore + ToolRegistry）。第四层(Subagent System)天枢的 Orchestrator 有基础但缺少 worktree 隔离和结果验证。

---

## 二、System Prompt 结构（最重要的借鉴）

Claude Code 的 system prompt 不是一堵墙——是**分层压缩的指令集**：

```
Layer 1: Identity + Safety (身份 + 安全红线, 不可覆盖)
Layer 2: Environment + Conventions (环境 + 约定, 项目特定)
Layer 3: Tools Quick Reference (工具速查, 按场景分组)
Layer 4: Iron Rules (铁律, 实操级约束)
Layer 5: Behavioral Guidelines (行为准则, 柔性)
```

### 2.1 天枢缺失的关键层

**Layer 2: Environment + Conventions 缺失：**

天枢的 soul.md 有"Windows 上运行"一节，但太简略。Claude Code 的做法：

```
运行环境
- OS: {detected_os}
- Shell: {shell_type}
- Workspace: {cwd}
- 文件操作约定: 读用 read_file, 写用 write_file, 不用 shell 做文件操作
- 已知的失败模式: web_search 对中文短查询效果差 → 用完整句子
- 工具降级链: tool_A 失败 → 试 tool_B (不要反复重试同一个)
```

**Layer 4: Iron Rules 缺失：**

天枢刚加了 6 条铁律，但 Claude Code 的铁律更多、更具体：

```
1. 够用就停
2. 同一工具+参数连续2次失败 → 换方法
3. 搜索不到直接说
4. 读文件用工具, 不用 shell
5. 工具互斥: 搜微信用 X, 别用 Y
6. browse 只能接 URL, 本地文件用 read_file
7. 工具失败时解释原因, 不默默换工具
8. 遇到不确定的任务先读 MASTER_ROUTING
9. 参数中的路径必须是绝对路径
10. 不要编造工具名——只使用上述清单中的工具
```

### 2.2 可照搬的 System Prompt 模板

```markdown
# 身份
你是 {agent_name} — {one_line_identity}

## 核心原则
{3-5 条不可妥协的底线}

## 运行环境
- OS: {detected}  Shell: {detected}
- 文件操作: 用 read_file/write_file, 不用 shell_exec
- 路径: 永远用绝对路径
- 已知问题: {platform_specific_gotchas}

## 工具速查
{按场景分组的工具表, 含互斥提示}

## 工具使用铁律
{10 条实操级约束}

## 行为准则
{柔性指南, 如"不确定时选保守方案"}
```

---

## 三、工具设计

### 3.1 工具定义模板

Claude Code 的工具定义精度远高于天枢：

```json
{
  "name": "web_search",
  "description": "Search the web. Returns titles, snippets, and URLs.\n\n"
    "Use this for: finding current information, fact-checking, research.\n"
    "Do NOT use for: searching WeChat articles (use sogou_weixin instead).\n"
    "Tips: use complete sentences for Chinese queries; short queries may return pinyin tutorials.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Search query. For Chinese, use complete sentences (not keywords). Example: '2026年AI应用收入趋势' not 'AI 收入'"
      },
      "count": {
        "type": "integer",
        "description": "Number of results. Default 5, max 10.",
        "default": 5
      }
    },
    "required": ["query"]
  }
}
```

**天枢的差距：**
- description 只有 1-2 句，没有"Use this for / Do NOT use for"
- 参数 description 没有 example
- 没有 `default` 值

### 3.2 工具分组

Claude Code 的工具按**操作类型**自然分组：

```
文件操作: Read, Write, Edit, Glob, Grep
终端:     Bash
Web:      WebSearch, WebFetch
Agent:    Task, Agent, Workflow
记忆:     (internal, 不暴露为工具)
```

天枢的 24 个工具是扁平列表，没有这种直观的分组。

---

## 四、权限模型（天枢已基本对齐）

Claude Code 的权限模型天枢已经实现：

| Level | Claude Code | 天枢 |
|-------|-----------|------|
| SAFE (0) | 直接执行 | 直接执行 |
| READ (1) | 直接执行 | 直接执行 |
| WRITE (2) | Panel + 方向键确认 | Panel + 方向键确认 |
| DANGER (3) | Panel + 红色边框 | Panel + 红色边框 + 策略引擎拦截 |

**天枢多余做的：** 策略引擎的硬禁止（rm -rf 直接 deny）。Claude Code 没有这个——它依赖 LLM 的自约束。

**天枢缺少的：** "始终允许"选项持久化。Claude Code 记住了用户的选择，天枢刚加上（tool_whitelist.json）。

---

## 五、上下文管理

### 5.1 压缩策略

Claude Code 的压缩策略和天枢类似（LLM 摘要中间段），但多了一个细节：

**压缩通知。** Claude Code 会在对话中插入一条可见的系统消息：

```
💾 Context compacted. Previous conversation summarized. 
   保留: 最近 5 轮对话
   压缩: 中间 12 轮对话 → 300 token 摘要
```

天枢已有压缩通知（`💾 上下文已压缩 (L2: 8500→1200字符)`），但缺少"保留了什么"的信息。

### 5.2 对话摘要注入

**这是天枢完全没有的。** Claude Code 在每轮工具调用后的下一轮对话开头，注入上一轮的工具调用摘要：

```
[上轮工具调用: web_search(query="AI应用趋势") → 3条结果, browse(url="36kr.com") → 成功]
```

这帮助 LLM 在长对话中记住自己做了什么，特别是在多次压缩后。天枢的压缩会丢掉这些信息。

### 5.3 Token 预算可视化

Claude Code 有内部 token 预算（不展示给用户），但 `/cost` 命令可以看到消耗。天枢刚加了 `/cost`。

---

## 六、Agent 编排（天枢差距最大的一块）

### 6.1 Worktree 隔离

Claude Code 的每个子 Agent 跑在独立的 git worktree 中：

```
主会话:  F:\tianshu\
子Agent1: F:\tianshu\.claude\worktrees\agent-1\
子Agent2: F:\tianshu\.claude\worktrees\agent-2\
```

如果子 Agent 没有产生有效改动，worktree 自动删除。这提供了**文件系统级的隔离**——子 Agent 的写操作不会污染主工作区。

天枢的 Orchestrator 没有这种隔离——所有子 Agent 共享同一个文件系统。

### 6.2 编排模式

Claude Code 支持三种：

```
pipeline:  A → B → C (每项独立走完所有阶段, 无 barrier)
parallel:  A | B | C (全部完成后才能继续)
Workflow:  脚本驱动的复杂拓扑 (循环/条件分支)
```

天枢只有串行（serial），缺 parallel 和条件分支。

### 6.3 结果验证

Claude Code 的 Workflow 系统支持：

- **Adversarial verify**: 3 个独立 reviewer 尝试证伪同一个发现
- **Completeness critic**: 一个 Agent 专门问"还有什么漏掉的"
- **Loop-until-dry**: 多轮发现直到连续 N 轮无新结果

天枢的 Orchestrator 没有验证层。

---

## 七、记忆系统

Claude Code 使用 Markdown 文件作为记忆（非 SQLite）：

```
~/.claude/projects/{project}/memory/
  ├── MEMORY.md       ← 记忆索引
  ├── {slug-1}.md     ← 单条记忆 (frontmatter + body)
  └── {slug-2}.md
```

每条记忆 = 一个 .md 文件，frontmatter 声明元数据，body 是内容。`[[wikilink]]` 做关联。

**优势：** 人类可读、git 可追踪、LLM 原生理解（Markdown）
**劣势：** 搜索比 SQLite 慢、无向量检索

天枢的 SQLite 方案搜索更快，但人类不可直接阅读。**两者可以互补：SQLite 做索引 + Markdown 做展示。**

---

## 八、Plan Mode

Claude Code 的 Plan Mode 是一个独立阶段：

```
1. 用户请求 → 系统判断是否需要 Plan Mode
2. 如果需要: 进入只读模式 (READ 工具)
3. LLM 探索代码库 → 生成计划 → 写入 plan file
4. 用户审批计划
5. 退出 Plan Mode → 进入实现模式
```

天枢的 Plan Mode 更简单——只是一个 prompt 暗示。可以学习 Claude Code 的**工具过滤**：Plan Mode 下只暴露 READ 级别工具。

---

## 九、Skill/Plugin 系统

Claude Code 的 Skill 系统天枢已经对齐得不错：

| 特性 | Claude Code | 天枢 |
|------|-----------|------|
| Markdown 格式 | SKILL.md (YAML frontmatter + body) | ✅ 完全对齐 |
| 触发方式 | 关键词自动匹配 + 手动 /skill-name | ✅ 关键词 + /learn |
| 工具声明 | 在 body 中描述，LLM 自行组合 | ✅ 相同 |
| 插件市场 | 有 | ❌ 无 |
| 用户自定义 | ~/.claude/skills/ | ✅ ~/.tianshu/skills/ |

**天枢缺少的：**
- Skill bundle（多个 skill 绑定为一个 slash command）
- 远程 skill 安装（从 GitHub 拉取）

---

## 十、天枢应立即实现的 6 项

| # | 借鉴 | 难度 | 代码量 |
|---|---|---|---|
| 1 | **System prompt 重构** — 按 5 层结构重写 soul.md | 低 | 0 代码, 纯文本 |
| 2 | **工具 description 加 Use/Don't Use + example** | 低 | 改 5-6 个 description 字符串 |
| 3 | **对话摘要注入** — 每轮开头注入上轮工具摘要 | 低 | service.py ~5 行 |
| 4 | **Plan Mode 工具过滤** — 只暴露 READ 工具 | 低 | ToolRegistry 已支持 mode=plan |
| 5 | **搜索结果截断标注** | 低 | web_search.py ~3 行 |
| 6 | **工具按类型分组展示** — /tools 命令输出 | 中 | ~30 行 |

---

## 十一、中期应实现的 3 项

| # | 借鉴 | 难度 |
|---|---|---|
| 7 | **Worktree 隔离** — 子Agent 独立文件系统 | 中 |
| 8 | **并行编排** — parallel/pipeline 拓扑 | 中 |
| 9 | **结果验证** — adversarial verify agent | 中 |
