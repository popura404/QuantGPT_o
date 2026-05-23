"""Integration tests for admin routes."""

import os
from unittest.mock import patch

import pytest

from quantgpt.auth import create_admin_token

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_auth_failure_buckets():
    from quantgpt.auth import _auth_failure_buckets

    _auth_failure_buckets.clear()
    yield
    _auth_failure_buckets.clear()


class TestAdminLogin:
    async def test_correct_password(self, client):
        resp = await client.post("/api/v1/admin/login", json={
            "password": "test-admin-pw",
        })
        assert resp.status_code == 200
        assert "token" in resp.json()

    async def test_wrong_password(self, client):
        resp = await client.post("/api/v1/admin/login", json={
            "password": "wrong-password",
        })
        assert resp.status_code == 401

    async def test_wrong_password_locks_after_repeated_failures(self, client):
        for _ in range(4):
            resp = await client.post("/api/v1/admin/login", json={
                "password": "wrong-password",
            })
            assert resp.status_code == 401

        locked = await client.post("/api/v1/admin/login", json={
            "password": "wrong-password",
        })
        assert locked.status_code == 429

        correct = await client.post("/api/v1/admin/login", json={
            "password": "test-admin-pw",
        })
        assert correct.status_code == 429

    async def test_successful_login_clears_prior_failures(self, client):
        for _ in range(4):
            resp = await client.post("/api/v1/admin/login", json={
                "password": "wrong-password",
            })
            assert resp.status_code == 401

        ok = await client.post("/api/v1/admin/login", json={
            "password": "test-admin-pw",
        })
        assert ok.status_code == 200

        next_wrong = await client.post("/api/v1/admin/login", json={
            "password": "wrong-password",
        })
        assert next_wrong.status_code == 401

    async def test_empty_admin_password_returns_503(self, client):
        with patch.dict(os.environ, {"QUANTGPT_ADMIN_PASSWORD": ""}):
            resp = await client.post("/api/v1/admin/login", json={
                "password": "anything",
            })
            assert resp.status_code == 503

    async def test_example_admin_password_returns_503(self, client):
        with patch.dict(os.environ, {"QUANTGPT_ADMIN_PASSWORD": "replace_with_a_strong_admin_password"}):
            resp = await client.post("/api/v1/admin/login", json={
                "password": "replace_with_a_strong_admin_password",
            })
            assert resp.status_code == 503


class TestAdminOverview:
    async def test_requires_admin_token(self, client, test_user, auth_headers):
        resp = await client.get("/api/v1/admin/overview", headers=auth_headers)
        assert resp.status_code == 403

    async def test_no_token_unauthorized(self, client):
        resp = await client.get("/api/v1/admin/overview")
        assert resp.status_code == 401

    async def test_admin_can_access(self, client, test_user):
        token = create_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/admin/overview", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "user_count" in data
        assert "task_count" in data
        assert "success_rate" in data


class TestAdminUsers:
    async def test_list_users(self, client, test_user):
        token = create_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/admin/users", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert data["total"] >= 1

    async def test_pagination(self, client, test_user):
        token = create_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/admin/users?page=1&page_size=1", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["users"]) <= 1
