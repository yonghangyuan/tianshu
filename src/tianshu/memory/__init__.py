"""Memory Service — L2 MEMORY.md + L5 SQLite FTS5.

L2 (持久文件): ~/.tianshu/memory/MEMORY.md, USER.md
L5 (全量检索): SQLite FTS5 跨会话语义检索
"""

from .service import MemoryService

__all__ = ["MemoryService"]
