"""Server 鉴权测试 — /run 等端点的登录保护。

P0 修复回归测试（2026-08-13，玉衡会话反馈）：
此前 /run、/run/stream、/tools、/audit、/memory、/skills、/models 无鉴权，
任何能访问端口的人都能烧 API 额度。修复后全部需要登录。

使用 fastapi TestClient，通过 monkeypatch 模块级配置（不实际启动 AgentCore，
鉴权通过后 /run 会 503——这恰好证明鉴权层已放行）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import tianshu.gateway.server as srv


@pytest.fixture
def client(monkeypatch):
    """配置登录密码 + 显式 SERVER_TOKEN，清空 token 集合。"""
    monkeypatch.setattr(srv, "LOGIN_PASSWORD", "test-pw")
    monkeypatch.setattr(srv, "SERVER_TOKEN", "static-token-123")
    srv._login_tokens.clear()
    yield TestClient(srv.app)
    srv._login_tokens.clear()


def _login(client) -> str:
    resp = client.post("/login", json={"username": "u", "password": "test-pw"})
    assert resp.status_code == 200
    return resp.json()["token"]


class TestRunAuth:
    def test_run_without_token_rejected(self, client):
        resp = client.post("/run", json={"input": "你好"})
        assert resp.status_code == 401

    def test_run_stream_without_token_rejected(self, client):
        resp = client.post("/run/stream", json={"input": "你好"})
        assert resp.status_code == 401

    def test_run_with_query_token_passes_auth(self, client):
        token = _login(client)
        resp = client.post(f"/run?token={token}", json={"input": "你好"})
        # 鉴权通过 → 503 (AgentCore 未初始化)；401 说明鉴权失败
        assert resp.status_code == 503

    def test_run_with_cookie_passes_auth(self, client):
        _login(client)  # set-cookie 后 TestClient 自动携带
        resp = client.post("/run", json={"input": "你好"})
        assert resp.status_code == 503

    def test_run_with_static_bearer_token(self, client):
        resp = client.post(
            "/run",
            json={"input": "你好"},
            headers={"Authorization": "Bearer static-token-123"},
        )
        assert resp.status_code == 503

    def test_run_with_login_token_bearer(self, client):
        token = _login(client)
        resp = client.post(
            "/run",
            json={"input": "你好"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503

    def test_run_with_wrong_token_rejected(self, client):
        resp = client.post(
            "/run",
            json={"input": "你好"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_login_wrong_password_rejected(self, client):
        resp = client.post("/login", json={"username": "u", "password": "错误密码"})
        assert resp.status_code == 401


class TestInfoEndpointsAuth:
    """信息类端点同样受保护。"""

    @pytest.mark.parametrize("path", [
        "/tools", "/audit", "/memory", "/skills", "/models",
    ])
    def test_rejected_without_token(self, client, path):
        assert client.get(path).status_code == 401

    @pytest.mark.parametrize("path", [
        "/tools", "/audit", "/memory", "/skills", "/models",
    ])
    def test_passes_with_token(self, client, path):
        token = _login(client)
        # 鉴权通过 → 503 (未初始化)；401 说明鉴权失败
        assert client.get(f"{path}?token={token}").status_code == 503


class TestHealthOpen:
    def test_health_needs_no_auth(self, client):
        # /health 供监控/nginx 探活，保持开放
        assert client.get("/health").status_code == 503  # 未初始化而非 401
