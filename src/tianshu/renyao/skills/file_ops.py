"""File Operations Skill — read_file / write_file / list_dir。

替代 shell_exec 做文件操作：跨平台、无确认弹窗（read）、结构化输出。
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import BaseSkill, SkillTool


class FileOpsSkill(BaseSkill):
    name = "file_ops"
    description = "安全读写文件和目录列表（跨平台，替代 shell ls/cat/echo）"
    trigram = "地"
    trigger_keywords = ["读文件", "写文件", "列出目录", "read file", "write file", "list dir"]

    def get_tools(self) -> list[SkillTool]:
        return [
            SkillTool(
                name="read_file",
                description="Read a file's content. Returns text with line count, encoding, and file size.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative file path"},
                        "offset": {"type": "integer", "description": "Start reading from this line (1-indexed)", "default": 1},
                        "limit": {"type": "integer", "description": "Max lines to read (default 200)", "default": 200},
                    },
                    "required": ["path"],
                },
                handler=self._read_file,
                permission_level=1,  # READ
            ),
            SkillTool(
                name="write_file",
                description="Write content to a file. Creates parent directories if needed. Overwrites existing files.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to write to"},
                        "content": {"type": "string", "description": "Content to write"},
                        "encoding": {"type": "string", "description": "File encoding (default utf-8)", "default": "utf-8"},
                    },
                    "required": ["path", "content"],
                },
                handler=self._write_file,
                permission_level=2,  # WRITE
            ),
            SkillTool(
                name="edit_file",
                description="Line-precise edit: replace old_string with new_string in a file. Prefer this over write_file for surgical changes. old_string must match exactly once (or use replace_all).",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to edit"},
                        "old_string": {"type": "string", "description": "Exact text to find and replace"},
                        "new_string": {"type": "string", "description": "Replacement text"},
                        "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false)", "default": False},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
                handler=self._edit_file,
                permission_level=2,  # WRITE
            ),
            SkillTool(
                name="list_dir",
                description="List files and directories in a path. Returns names, sizes, and types.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path to list (default: current dir)"},
                        "pattern": {"type": "string", "description": "Optional glob pattern, e.g. '*.py'"},
                        "max_items": {"type": "integer", "description": "Max items to return (default 50)", "default": 50},
                    },
                    "required": [],
                },
                handler=self._list_dir,
                permission_level=0,  # SAFE
            ),
            SkillTool(
                name="share_file",
                description="Copy a server file to the web chat's download directory, making it available for other users to download. Use this when a user asks to share or upload a file from the server to the chat.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute path of the file to share"},
                    },
                    "required": ["path"],
                },
                handler=self._share_file,
                permission_level=2,  # WRITE
            ),
        ]

    async def _share_file(self, path: str, **kwargs) -> str:
        """复制文件到上传目录，使其在群聊中可下载。"""
        import shutil as _shutil, httpx
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"❌ 文件不存在: {p}"
        if p.is_dir():
            return f"❌ 不能分享目录: {p}"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    "http://127.0.0.1:8720/chat/share",
                    params={"path": str(p)},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return f"✅ 已分享到群聊: {data['filename']} ({data['size']:,} bytes)"
                else:
                    return f"❌ 分享失败: HTTP {resp.status_code}"
        except Exception as e:
            return f"❌ 分享失败: {e}"

    # ── Implementations ─────────────────────────────────────────────

    async def _read_file(
        self, path: str, offset: int = 1, limit: int = 200, **kwargs
    ) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            # 尝试智能提示：检查同级文件
            parent = p.parent
            if parent.exists():
                siblings = [x.name for x in parent.iterdir() if x.is_file()][:8]
                hint = f"\n附近文件: {', '.join(siblings)}" if siblings else ""
                return f"❌ 文件不存在: {p}{hint}"
            return f"❌ 文件不存在: {p}"

        if p.is_dir():
            return f"❌ 路径是目录而非文件: {p}\n提示: 用 list_dir 列出目录内容"

        # 检测是否为二进制文件
        try:
            with open(p, "r", encoding="utf-8") as f:
                f.read(1)
        except UnicodeDecodeError:
            size = p.stat().st_size
            return (
                f"⚠️  二进制文件（无法用文本读取）\n"
                f"路径: {p}\n"
                f"大小: {_format_size(size)}"
            )

        # 读取内容
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:
            return f"❌ 读取失败: {e}"

        lines = text.splitlines()
        total_lines = len(lines)
        size = p.stat().st_size

        # 截取
        start = max(0, offset - 1)
        end = min(len(lines), start + limit)
        selected = lines[start:end]

        # 格式化输出
        result = f"📄 {p.name}  ({total_lines} 行, {_format_size(size)})\n"
        result += f"{'─' * 60}\n"
        for i, line in enumerate(selected, start=start + 1):
            line_display = line[:200] + ("…" if len(line) > 200 else "")
            result += f"  {i:4d} │ {line_display}\n"

        if end < total_lines:
            result += (
                f"{'─' * 60}\n"
                f"… 省略了 {total_lines - end} 行 "
                f"(共 {total_lines} 行, 显示 {start + 1}-{end})\n"
                f"提示: 用 offset={end + 1} 继续读取"
            )

        return result

    async def _write_file(
        self, path: str, content: str, encoding: str = "utf-8", **kwargs
    ) -> str:
        p = Path(path).expanduser().resolve()

        # 安全检查：不覆盖目录
        if p.exists() and p.is_dir():
            return f"❌ 目标路径是目录: {p}"

        # 创建父目录
        p.parent.mkdir(parents=True, exist_ok=True)

        existed = p.exists()
        try:
            p.write_text(content, encoding=encoding)
        except Exception as e:
            return f"❌ 写入失败: {e}"

        size = p.stat().st_size
        verb = "已覆盖" if existed else "已创建"
        return f"✅ {verb}: {p}\n   {len(content)} 字符, {_format_size(size)}"

    async def _edit_file(
        self, path: str, old_string: str, new_string: str, replace_all: bool = False, **kwargs
    ) -> str:
        """行级精确替换——外科手术式修改，避免整文件重写。"""
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"❌ 文件不存在: {p}"
        if p.is_dir():
            return f"❌ 路径是目录而非文件: {p}"

        # 读文件：utf-8 优先，失败回退 gbk（Windows 常见编码）
        # newline="" 不做换行转换——LF/CRLF 原样读回，行级编辑不弄乱换行
        encoding = "utf-8"
        try:
            with open(p, "r", encoding="utf-8", newline="") as f:
                text = f.read()
        except UnicodeDecodeError:
            try:
                with open(p, "r", encoding="gbk", errors="replace", newline="") as f:
                    text = f.read()
                encoding = "gbk"
            except Exception as e:
                return f"❌ 读取失败: {e}"

        count = text.count(old_string)
        if count == 0:
            snippet = old_string[:50] + ("…" if len(old_string) > 50 else "")
            return (
                f"❌ 未找到目标文本: {snippet}\n"
                f"提示: 用 read_file 核对文件内容（注意空格/换行/转义）"
            )
        if count > 1 and not replace_all:
            return (
                f"❌ 找到 {count} 处匹配，需要唯一匹配。\n"
                f"提示: 提供更长的 old_string 上下文，或设置 replace_all=true"
            )

        new_text = text.replace(old_string, new_string) if replace_all else \
            text.replace(old_string, new_string, 1)
        if new_text == text:
            return "(无变更)"

        try:
            with open(p, "w", encoding=encoding, newline="") as f:
                f.write(new_text)
        except Exception as e:
            return f"❌ 写入失败: {e}"

        # 返回 diff 供用户查看
        from tianshu.diyao.diff import compute_diff
        diff = compute_diff(text, new_text, str(p))
        n = new_text.count(new_string) if new_string else 0
        return f"✅ 已替换 {n if replace_all else 1} 处: {p}\n--- Diff ---\n{diff}"

    async def _list_dir(
        self, path: str = ".", pattern: str = "", max_items: int = 50, **kwargs
    ) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"❌ 目录不存在: {p}"
        if not p.is_dir():
            return f"❌ 路径不是目录: {p}\n提示: 用 read_file 读取文件内容"

        # 收集条目
        entries = []
        try:
            for entry in p.iterdir():
                if pattern and not entry.match(pattern):
                    continue
                try:
                    stat = entry.stat()
                    entries.append({
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    })
                except OSError:
                    entries.append({
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "size": 0,
                        "mtime": 0,
                    })
        except PermissionError as e:
            return f"❌ 权限不足: {e}"

        if not entries:
            pattern_hint = f' （匹配 "{pattern}"）' if pattern else ""
            return f"📂 {p}\n   空目录{pattern_hint}"

        # 排序：目录在前，按名字
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        entries = entries[:max_items]

        # 统计
        dir_count = sum(1 for e in entries if e["is_dir"])
        file_count = len(entries) - dir_count

        import time
        result = (
            f"📂 {p}\n"
            f"   {dir_count} 个目录, {file_count} 个文件"
        )
        if pattern:
            result += f' (匹配 "{pattern}")'
        result += "\n" + "─" * 60 + "\n"

        for e in entries:
            icon = "📁" if e["is_dir"] else "📄"
            name = e["name"]
            size = _format_size(e["size"]) if not e["is_dir"] else "-"
            mt = time.strftime("%m-%d %H:%M", time.localtime(e["mtime"])) if e["mtime"] else ""
            result += f"  {icon} {name:<40s} {size:>8s}  {mt}\n"

        if len(entries) >= max_items:
            result += f"\n… 条目过多，只显示了前 {max_items} 个\n"

        return result


def _format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size}{unit}"
        size //= 1024
    return f"{size}TB"
