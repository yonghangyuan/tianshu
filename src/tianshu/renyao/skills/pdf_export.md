---
name: pdf-export
description: PDF 生成与工具箱
trigram: 地
permission: WRITE
tools: [md_to_pdf, html_to_pdf, pdf_merge, pdf_split, pdf_info]
trigger_keywords: [生成pdf, 导出pdf, 转pdf, 合并pdf, 拆分pdf]
---
# PDF Export Skill
Markdown/HTML → PDF（本机 Edge headless 渲染）+ pypdf 工具箱。

## 方案
- **创建**: Edge `--headless --print-to-pdf`，与 browser skill 同一引擎，零外部下载
- **中文**: Windows 系统字体（微软雅黑），无 reportlab 的 CJK 字体注册问题
- **排版**: 内置 A4 打印模板 CSS（`@page` 控制页边距、标题/表格/代码样式、`--no-pdf-header-footer`）
- **工具箱**: pypdf 惰性导入 — merge/split/info（对标 Hermes pdf skill，跳过 qpdf/poppler 外部工具）

## 依赖
`pip install 'tianshu[pdf]'`（markdown + pypdf，均为纯 Python）。
未装 markdown → md_to_pdf 报错提示；未装 pypdf → merge/split/info 报错提示。

## 坑（来自 Claude Code 社区实测）
- 漏 `--no-pdf-header-footer` 会打印浏览器页眉页脚
- headless 渲染可能静默失败（exit 0 但 CJK 成方块）→ `_verify` 检查产物大小 + 页数
