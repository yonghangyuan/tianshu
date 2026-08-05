# Claude Code 设计模式 — 天枢可借鉴的点

> 基于使用观察和公开信息分析，2026-08-05

---

## 一、System Prompt 结构

Claude Code 的 system prompt 是一个**三层压缩结构**：

```
1. Identity + Principles (身份 + 核心原则, ~20%)
2. Environment + Tools Quick Reference (环境 + 工具速查表, ~40%)
3. Iron Rules + Behavioral Guidelines (铁律 + 行为准则, ~40%)
```

**天枢目前缺失的：**

- 没有"铁律"部分。天枢的 soul.md 有"行为准则"但偏抽象(决策可审计)，缺少实操级约束
- 工具速查是扁平列表，没有按场景分组

**可照搬的：**

```markdown
## 工具使用铁律
1. 够用就停 — 搜到 3-5 条有用信息后立刻组织回答
2. 同一工具+相同参数连续失败 2 次 → 立刻停，换方法
3. 搜索不到不是你的错 — 直接告诉用户你能找到什么
4. shell_exec 失败 → 改用 file_ops
```

---

## 二、工具定义粒度

Claude Code 的工具比天枢**更窄、更深**：

| 维度 | Claude Code | 天枢 |
|------|-----------|------|
| 工具数 | ~20 | 24 |
| 每个工具的 description | 3-5 句，含使用时机和禁忌 | 1-2 句 |
| 参数 description | 每个参数有示例值 | 只有 schema，无示例 |
| 失败处理 | 内建重试+降级建议 | 抛回 LLM |
| 互斥提示 | 明确写"不要用 X 做 Y" | 没有 |

**可照搬的：**
- 每个 tool description 末尾加一行："什么时候不要用这个工具"
- 参数加 `example` 字段（JSON Schema 支持，DeepSeek 也兼容）
- 工具分组加互斥提示（"搜微信用 sogou_weixin，不要用 web_search"）

---

## 三、权限分级策略

Claude Code 的权限模型天枢已经基本对齐，但缺两个细节：

**1. 权限确认的 UI 层次**

```
SAFE (0):  无提示，直接执行
READ (1):  无提示，直接执行
WRITE (2): Rich Panel + 方向键选择 [y]执行 [n]跳过 [a]始终允许
DANGER (3): Rich Panel + 红色边框 + 方向键 + "此操作不可逆"
```

天枢已经实现了 Panel + 方向键（cli.py `_handle_confirm`），但缺少"始终允许"选项。

**2. 白名单持久化**

Claude Code 记住用户的"always allow"选择。天枢的 `_permission_whitelist` 是内存 set，重启丢失。

---

## 四、上下文管理

Claude Code 有几个天枢没做的：

**1. 对话摘要注入。** 每轮对话开头，Claude Code 注入前一轮的工具调用摘要——"上一轮你调用了 web_search，返回了 3 条结果"——让 LLM 知道自己做了什么。

**2. 工具结果的 size budget。** 搜索结果超过 N 条时自动截断标注——"还有 15 条结果未展示"——保护上下文窗口。

**3. @引用支持多种格式。** `@file.py`、`@file.py:42`、`@dir/`——天枢已经有了。

---

## 五、可立即照搬的具体改动（按性价比）

### 1. soul.md 加"铁律"部分（0 代码）
```markdown
## 工具使用铁律
1. 够用就停 — 搜到 3-5 条有用信息就组织回答，不要追"全"
2. 同一工具+相同参数连续失败 2 次 → 换方法
3. 搜索不到直接告诉用户，不要反复换搜索引擎重试
4. 优先用 file_ops (read/write/list)，shell_exec 只用于无可替代时
```

### 2. 工具 description 加互斥提示（改 3 个文件）
```python
# web_search.py
description = "搜索网页。搜微信文章请用 sogou_weixin，不要用此工具"
# sogou_weixin 不需要改（它是唯一的微信搜索工具）
```

### 3. 每轮开头注入上一轮工具摘要（改 service.py ~5 行）
```python
# run_stream() 中, _build_messages() 之后:
if ctx.messages and last_turn_tools:
    messages.append({
        "role": "system",
        "content": f"[上一轮工具调用: {', '.join(last_turn_tools)}]"
    })
```

### 4. 白名单持久化（改 service.py ~10 行）
```python
# setup() 中加载, confirm_tool('always') 时写入
# 存储: ~/.tianshu/tool_whitelist.json
```

### 5. 搜索结果截断标注（改 web_search.py ~3 行）
```python
if len(results) > 10:
    output += f"\n... 还有 {len(results)-10} 条结果未展示"
```

---

## 总结

天枢和 Claude Code 在架构层已经对齐（三爻 ≈ Claude Code 的内置护栏）。差距在**细节打磨**——system prompt 的实操性、工具 description 的互斥提示、白名单持久化、搜索结果截断。这些都是 0-5 行代码的小改动，但累积起来就是专业感和业余感的差别。
