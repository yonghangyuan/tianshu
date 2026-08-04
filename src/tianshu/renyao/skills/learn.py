"""/learn 命令 —— 用户主动触发，LLM 自生成 SKILL.md。

借鉴 Hermes Agent 的 learn_prompt.py（150行 prompt）。
核心理念：不是自动生成技能——是用户主动触发、LLM 用已有工具
收集上下文、写出 SKILL.md、用户审查后可立即使用。

用法:
    /learn 把刚才搜索论文并写笔记的操作变成技能
    /learn 以后每次说"帮我查天气"就用这个流程
"""

from __future__ import annotations


def build_learn_prompt(
    description: str,
    recent_tools: list[str],
    recent_conversation: str,
    available_tools: list[str],
) -> str:
    """构建 /learn 的 system prompt——引导 LLM 生成 SKILL.md。

    Args:
        description: 用户对技能的自然语言描述
        recent_tools: 最近使用的工具名列表
        recent_conversation: 最近对话摘要
        available_tools: 系统中可用的所有工具
    """
    tools_list = "\n".join(f"  - {t}" for t in available_tools[:30])

    return f"""你正在帮助用户创建一个新的天枢 Skill。

用户想创建一个技能，描述如下:
  "{description}"

最近使用的工具:
  {", ".join(recent_tools) if recent_tools else "(无)"}

最近对话上下文:
  {recent_conversation[:2000] if recent_conversation else "(无)"}

系统中可用的工具:
{tools_list}

---

## 你的任务

写一个 SKILL.md 文件，定义这个新技能。格式如下:

```markdown
---
name: <技能英文名, 用下划线连接, 如 weather_query>
description: <一句话描述这个技能做什么>
trigram: 地          # 地(执行) / 人(决策) / 天(治理)
trigger_keywords:
  - <中文关键词1>
  - <中文关键词2>
tools:
  - <需要调用的工具名1>
  - <需要调用的工具名2>
version: 1
---

# <技能中文名>

## 触发条件
当用户说 "<触发场景>" 时激活此技能。

## 执行步骤
1. <第一步: 调用什么工具, 什么参数>
2. <第二步: 处理结果>
3. <第三步: 输出格式>
```

## 规则

1. **name 必须是英文**，用下划线连接，简洁明了
2. **trigram**: 如果只是执行工具→返回结果，写"地"；如果需要判断/决策，写"人"；如果涉及规则/约束，写"天"
3. **trigger_keywords**: 列出 3-5 个用户可能说的中文关键词
4. **tools**: 只列出实际需要的工具名（从上述"可用工具"中选择）
5. **执行步骤**: 写清楚每一步做什么，LLM 靠这段描述就能执行

## 输出

**只输出 SKILL.md 的完整内容**，不要输出任何其他文字。
从 `---` 开始，到最后的步骤描述结束。

现在，为用户的技能 "{description}" 生成 SKILL.md:"""


def parse_skill_md(raw: str) -> tuple[dict, str] | None:
    """从 LLM 回复中解析 SKILL.md。

    Returns:
        (frontmatter_dict, body_text) 或 None
    """
    import yaml
    import re

    # 提取 frontmatter 块
    text = raw.strip()
    if not text.startswith("---"):
        # LLM 可能在前面加了说明文字，尝试找到第一个 ---
        match = re.search(r"---\n", text)
        if match:
            text = text[match.start():]

    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        return None

    if not isinstance(meta, dict) or not meta.get("name"):
        return None

    return meta, parts[2].strip()
