"""Phase 4: Session Persistence 测试。"""

import gc
import sys
import tempfile
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root / "src"))

from tianshu.memory.session_store import SessionStore
from tianshu.sdk.models import AgentContext


class TestSessionStore:
    """测试 SessionStore。"""

    @staticmethod
    def _cleanup_store(store, db_path):
        """确保 SQLite 连接关闭后再删除 temp 文件（Windows 兼容）。"""
        del store
        gc.collect()
        try:
            Path(db_path).unlink(missing_ok=True)
        except PermissionError:
            pass  # Windows 偶尔持锁，忽略

    def test_save_and_load(self):
        """保存→加载往返。"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store = None
        try:
            store = SessionStore(db_path)
            ctx = AgentContext(
                session_id="test-001",
                messages=[
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "你好！"},
                ],
                metadata={"model_override": "deepseek/v4-pro"},
            )
            store.save(ctx, title="测试会话")

            loaded = store.load("test-001")
            assert loaded is not None
            assert loaded.session_id == "test-001"
            assert len(loaded.messages) == 2
            assert loaded.messages[0]["content"] == "你好"
            assert loaded.metadata["model_override"] == "deepseek/v4-pro"
        finally:
            self._cleanup_store(store, db_path)

    def test_list_sessions(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store = None
        try:
            store = SessionStore(db_path)
            ctx1 = AgentContext(session_id="sess-1", messages=[
                {"role": "user", "content": "hello"},
            ])
            ctx2 = AgentContext(session_id="sess-2", messages=[
                {"role": "user", "content": "世界"},
            ])
            store.save(ctx1)
            store.save(ctx2)

            sessions = store.list_sessions()
            assert len(sessions) >= 2
            ids = [s["id"] for s in sessions]
            assert "sess-1" in ids
            assert "sess-2" in ids
        finally:
            self._cleanup_store(store, db_path)

    def test_delete_session(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store = None
        try:
            store = SessionStore(db_path)
            ctx = AgentContext(session_id="to-delete")
            store.save(ctx)
            assert store.load("to-delete") is not None

            ok = store.delete("to-delete")
            assert ok is True
            assert store.load("to-delete") is None
        finally:
            self._cleanup_store(store, db_path)

    def test_load_missing_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store = None
        try:
            store = SessionStore(db_path)
            assert store.load("nonexistent") is None
        finally:
            self._cleanup_store(store, db_path)

    def test_count(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store = None
        try:
            store = SessionStore(db_path)
            assert store.count() == 0
            store.save(AgentContext(session_id="a"))
            store.save(AgentContext(session_id="b"))
            assert store.count() == 2
        finally:
            self._cleanup_store(store, db_path)

    def test_shell_handle_not_serialized(self):
        """持久 shell 句柄不入库——resume 后为 None，由极简模式惰性重建。"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store = None
        try:
            store = SessionStore(db_path)
            ctx = AgentContext(session_id="with-shell", shell=object())
            store.save(ctx)

            loaded = store.load("with-shell")
            assert loaded is not None
            assert loaded.shell is None
        finally:
            self._cleanup_store(store, db_path)

    def test_update_existing(self):
        """对已存在的会话 save 应更新而非新增。"""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store = None
        try:
            store = SessionStore(db_path)
            ctx = AgentContext(session_id="update-me", messages=[
                {"role": "user", "content": "v1"},
            ])
            store.save(ctx)

            ctx.messages.append({"role": "assistant", "content": "v2"})
            store.save(ctx)

            assert store.count() == 1
            loaded = store.load("update-me")
            assert loaded is not None
            assert len(loaded.messages) == 2
            assert loaded.messages[1]["content"] == "v2"
        finally:
            self._cleanup_store(store, db_path)
