---
name: file_ops
description: 安全读写文件和目录列表——跨平台，替代shell ls/cat/echo
trigram: 地
permission: SAFE  # read_file=READ, list_dir=SAFE, write_file/share_file=WRITE
tools: [read_file, write_file, list_dir, share_file]
trigger_keywords: [读文件, 写文件, 列出目录, 分享文件]
---
# File Ops Skill
跨平台文件操作。read_file(带行号+编码检测)、write_file(自动建目录)、list_dir(模式过滤+排序)、share_file(复制到群聊下载目录)。
