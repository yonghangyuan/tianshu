# 天枢 · 技能路由总表

> 学 reverse-skill: MASTER-ROUTING.md 先查 → 具体 SKILL.md 再执行

## 路由规则

1. 用户意图 → 匹配 trigger_keywords → 路由到对应 SKILL.md
2. 模糊匹配 → 查本表 `related` 列 → 打开相关 SKILL.md
3. 未知意图 → 直接 ReAct 循环，不路由

## 技能清单

| 技能 | 三爻 | 权限 | 工具数 | 触发词 |
|------|:---:|:---:|:---:|------|
| [browser](browser.md) | 地 | SAFE/WRITE | 4 | 浏览/打开网页/下载/上传 |
| [web_search](web_search.md) | 地 | SAFE | 1 | 搜索/查一下/帮我搜 |
| [file_ops](file_ops.md) | 地 | SAFE/WRITE | 4 | 读文件/写文件/列出目录/分享 |
| [intel](intel.md) | 天 | SAFE/WRITE | 2 | 情报/搜集/监控/简报 |
| [shell](shell.md) | 地 | WRITE | 1 | 运行/执行/shell/命令 |
| [paper_radar](paper_radar.md) | 天 | SAFE/WRITE | 3 | 论文/paper/下载/arXiv |
| [trend_track](trend_track.md) | 天 | SAFE/WRITE | 2 | 趋势/热点/周报 |
| [code_assist](code_assist.md) | 人 | SAFE/WRITE | 2 | 项目/文档/代码 |
| [translate](translate.md) | 人 | SAFE | 1 | 翻译/translate |
| [schedule](schedule.md) | 人 | SAFE/WRITE | 3 | 日程/提醒/日历 |
| [image_gen](image_gen.md) | 地 | WRITE | 1 | 图片/生成/image |
