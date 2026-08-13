---
name: pdf-export
description: PDF 生成与工具箱（对标 Hermes pdf skill）
trigram: 地
permission: WRITE
tools: [md_to_pdf, html_to_pdf, pdf_merge, pdf_split, pdf_info, pdf_rotate, pdf_extract_text, pdf_extract_images, pdf_watermark, pdf_encrypt, pdf_decrypt, pdf_form_fields, pdf_fill_form]
trigger_keywords: [生成pdf, 导出pdf, 转pdf, 合并pdf, 拆分pdf, 水印, 加密pdf, pdf表单]
---
# PDF Export Skill
Markdown/HTML → PDF（本机 Edge headless 渲染）+ pypdf 工具箱，对标 Hermes pdf skill 的核心操作。

## 方案
- **创建**: Edge `--headless --print-to-pdf`，与 browser skill 同一引擎，零外部下载
- **中文**: Windows 系统字体（微软雅黑），无 reportlab 的 CJK 字体注册问题
- **排版**: 内置 A4 打印模板 CSS（`@page` 控制页边距、标题/表格/代码样式、`--no-pdf-header-footer`）
- **工具箱**: pypdf 惰性导入 — merge/split/rotate/extract/watermark/encrypt/decrypt/form
- **水印**: Edge 按原页尺寸（CSS pt = PDF pt = 1/72 inch）渲染水印页 → pypdf merge_page

## 与 Hermes pdf skill 的差异
| 能力 | Hermes | 天枢 |
|------|--------|------|
| 创建 PDF | reportlab | Edge 渲染（中文质量更好） |
| 合并/拆分/旋转 | pypdf + qpdf | pypdf |
| 文本提取 | pdfplumber | pypdf（表格结构化未覆盖） |
| 水印/加密/表单 | pypdf + 脚本 | pypdf |
| OCR 扫描件 | pytesseract + poppler | ❌ 不做（外部二进制，违反零外部下载） |

## 依赖
`pip install 'tianshu[pdf]'`（markdown + pypdf，均为纯 Python）。

## 坑（实测）
- 漏 `--no-pdf-header-footer` 会打印浏览器页眉页脚
- **Edge SingletonLock**: 连发渲染共用 user-data-dir 时第二个实例挂到第一个进程 → 每次调用独立目录
- **mkstemp fd**: 创建临时文件必须立即 `os.close(fd)`，否则 Windows 打开的句柄阻止 Edge 写入
- 命名 `@page`（`page:` CSS 属性）Edge 不支持 → 用统一 @page + 精确尺寸 div 分页
- headless 渲染可能静默失败（exit 0 但 CJK 成方块）→ `_verify` 检查产物大小 + 页数，pypdf 提取文本复核
