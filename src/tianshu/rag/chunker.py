"""文本分块器 — Markdown 感知 + 滑动窗口兜底。

策略:
  1. 按 Markdown 标题 (# ~ ####) 分段，标题作为 chunk 的 title
  2. 段内按空行分段，贪心拼接到目标 size
  3. 无段落边界的超长文本按句滑动窗口切分，保留 overlap 重叠
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;\n])\s*")


@dataclass
class Chunk:
    """一个文本片段。chunk_id 由 source+index+内容前缀确定性生成，幂等摄入。"""
    text: str
    source: str = ""
    title: str = ""
    index: int = 0

    @property
    def chunk_id(self) -> str:
        digest = f"{self.source}|{self.index}|{self.text[:128]}".encode("utf-8")
        return hashlib.sha1(digest).hexdigest()[:16]


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def _hard_split(text: str, size: int, overlap: int) -> list[str]:
    """硬切超长单句——固定窗口 + 重叠。"""
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    return [text[i:i + size] for i in range(0, len(text), step)]


def _sliding_chunks(text: str, size: int, overlap: int) -> list[str]:
    """按句滑动窗口切分：超 size 时 flush，携带尾部 overlap 作为下一块的开头。"""
    sentences = _split_sentences(text)
    if not sentences:
        return []
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        if len(s) > size:
            # 单句超长 → 先 flush buf 再硬切该句
            if buf.strip():
                chunks.append(buf.strip())
                buf = ""
            chunks.extend(_hard_split(s, size, overlap))
            continue
        if buf and len(buf) + len(s) + 1 > size:
            chunks.append(buf.strip())
            # 携带尾部句子作为重叠（总长不超过 overlap）
            carry, tail = "", 0
            for prev in reversed(_split_sentences(buf)):
                if tail + len(prev) + 1 > overlap:
                    break
                carry = prev + carry
                tail += len(prev)
            buf = carry
        buf += s
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def _chunk_section(section: str, source: str, title: str,
                   size: int, overlap: int) -> list[Chunk]:
    """段落级贪心分块；超长段落走滑动窗口。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section) if p.strip()]
    result: list[Chunk] = []
    buf = ""
    for p in paragraphs:
        if len(p) > size:
            if buf.strip():
                result.append(Chunk(text=buf.strip(), source=source, title=title))
                buf = ""
            for piece in _sliding_chunks(p, size, overlap):
                result.append(Chunk(text=piece, source=source, title=title))
            continue
        buf = (buf + "\n\n" + p).strip() if buf else p
        if len(buf) > size:
            result.append(Chunk(text=buf.strip(), source=source, title=title))
            buf = ""
    if buf.strip():
        result.append(Chunk(text=buf.strip(), source=source, title=title))
    return result


def chunk_text(text: str, source: str = "", size: int = 800,
               overlap: int = 120) -> list[Chunk]:
    """将文本切分为 chunk 列表。

    Args:
        text: 原始文本 (Markdown / 纯文本 / 代码)
        source: 来源标识 (文件路径等)
        size: 目标 chunk 字符数
        overlap: 滑动窗口重叠字符数 (仅超长段落生效)

    Returns:
        Chunk 列表，index 从 0 顺序编号。
    """
    if size <= overlap:
        raise ValueError(f"size ({size}) 必须大于 overlap ({overlap})")
    chunks: list[Chunk] = []
    matches = list(_HEADING_RE.finditer(text))
    if matches:
        head = text[:matches[0].start()]
        if head.strip():
            chunks.extend(_chunk_section(head, source, "", size, overlap))
        for i, m in enumerate(matches):
            title = m.group(2).strip()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunks.extend(_chunk_section(text[m.end():end], source, title, size, overlap))
    else:
        chunks.extend(_chunk_section(text, source, "", size, overlap))
    for i, c in enumerate(chunks):
        c.index = i
    return chunks
