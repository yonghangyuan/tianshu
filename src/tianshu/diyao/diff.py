"""统一 diff 工具 — service.py 与 file_ops.py 共用（避免循环导入）。

注意：本模块不得 import tianshu 其他包内模块。
core/__init__.py 导入 core.service，file_ops 若导入 core 会成环，
因此共享助手放在地爻层（diyao 只依赖 providers/sandbox/sdk）。
"""


def compute_diff(old: str, new: str, filepath: str = "", context_lines: int = 3) -> str:
    """生成 unified diff——写文件前预览变更。"""
    import difflib
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    # 确保末尾有换行（difflib 要求）
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filepath}" if filepath else "a/old",
        tofile=f"b/{filepath}" if filepath else "b/new",
        n=context_lines,
    )
    result = "".join(diff)
    if not result:
        return "(无变更)"
    return result
