---
name: intel
description: 情报搜集与分析——多源搜索、去重、提取关键信息、生成简报
trigram: 天
permission: SAFE  # intel_search=SAFE, intel_brief=WRITE
version: 0.1.0
tools:
  - intel_search
  - intel_brief
trigger_keywords:
  - 情报
  - 搜集
  - 监控
  - 追踪
  - 简报
  - intel
  - 动态
related:
  - web_search
  - file_ops
  - browser
---

# Intel Skill

情报搜集与分析。一条命令完成: 多源搜索 → 去重 → 关键信息提取 → 结构化简报。

## 何时使用

- 用户要求"监控"某个话题的最新动态
- 用户要求"搜集"信息并生成"简报"或"周报"
- 需要多源搜索（微信+网页+arXiv）并去重

## 工具

### intel_search

多源情报搜索。搜索微信+arXiv+网页，去重后返回结构化结果。

参数:
- `query` (必填): 搜索关键词
- `sources` (可选): 来源，逗号分隔。默认 "weixin,web"。可选: weixin, web, arxiv, rand
- `days` (可选): 回溯天数，默认 7
- `max_results` (可选): 每源最大结果数，默认 5

### intel_brief

生成结构化情报简报，保存为 .md 文件。

参数:
- `topic` (必填): 简报主题
- `items_json` (必填): JSON 数组，每项 {title, url, source, date, summary}
- `output_dir` (可选): 输出目录，默认 "F:/reports"

## 工作流

1. 用户描述情报需求
2. 调用 `intel_search` 多源搜索
3. 展示结果给用户确认
4. 调用 `intel_brief` 生成 .md 简报

## 依赖

- web_search Skill（网页搜索）
- browser Skill（搜狗微信搜索）
- 无外部 API 依赖
