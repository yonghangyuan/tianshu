---
name: browser
description: 浏览网页、下载文件、上传文件、搜狗微信搜索——零外部依赖的自有浏览器
trigram: 地
permission: SAFE  # browse=SAFE, download/upload=WRITE
tools: [browse, download, upload, sogou_weixin]
trigger_keywords: [浏览, 打开网页, 下载, 上传, browse, download, 微信文章]
related: [web_search, file_ops]
---
# Browser Skill
自有浏览器——httpx快速抓取+Edge CDP渲染JS页面。零外部下载。
## 工具
### browse — 打开网页（阅读模式，去广告和脚本）
### download — 下载文件到本地
### upload — 上传本地文件到服务器
### sogou_weixin — 搜狗微信搜索，返回标题+摘要
