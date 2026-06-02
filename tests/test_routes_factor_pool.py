"""Integration tests for factor-pool routes."""

import uuid

import pytest

from quantgpt.auth import create_access_token
from quantgpt.models import User

pytestmark = pytest.mark.asyncio


async def test_save_list_filter_and_facets(client, test_user, auth_headers):
    resp = await client.post(
        "/api/v1/factor-pool",
        json={
            "expression": "rank(close)",
            "name": "Close rank",
            "tags": ["Quality", "short horizon"],
            "category": "Momentum",
            "pool_status": "accepted",
            "universe": "csi500",
            "holding_period": 10,
            "metrics": {"score": 81},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    created = resp.json()
    entry = created["entry"]
    assert created["created"] is True
    assert entry["pool_status"] == "accepted"
    assert entry["category_tag"] == "category:momentum"
    assert entry["tags"] == ["category:momentum", "quality", "short horizon"]
    assert entry["factor_hash"]

    filtered = await client.get(
        "/api/v1/factor-pool",
        params=[("status", "accepted"), ("category", "momentum"), ("tags", "quality"), ("tags", "short horizon")],
        headers=auth_headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["entries"][0]["id"] == entry["id"]

    facets = await client.get("/api/v1/factor-pool/tags", headers=auth_headers)
    assert facets.status_code == 200
    data = facets.json()
    assert {"category_tag": "category:momentum", "category": "momentum", "count": 1} in data["categories"]
    assert {"tag": "quality", "count": 1} in data["tags"]


async def test_upsert_updates_existing_factor_identity(client, test_user, auth_headers):
    first = await client.post(
        "/api/v1/factor-pool",
        json={"expression": "ts_mean(close, 5)", "category": "mean", "universe": "hs300"},
        headers=auth_headers,
    )
    assert first.status_code == 201
    first_entry = first.json()["entry"]

    second = await client.post(
        "/api/v1/factor-pool",
        json={
            "expression": "ts_mean(close, 5)",
            "category": "mean",
            "universe": "hs300",
            "pool_status": "watchlist",
            "note": "same identity should update",
        },
        headers=auth_headers,
    )
    assert second.status_code == 200
    data = second.json()
    assert data["created"] is False
    assert data["entry"]["id"] == first_entry["id"]
    assert data["entry"]["note"] == "same identity should update"

    listed = await client.get("/api/v1/factor-pool", headers=auth_headers)
    assert listed.json()["total"] == 1


async def test_update_delete_and_user_isolation(client, db_session, test_user, auth_headers):
    create_resp = await client.post(
        "/api/v1/factor-pool",
        json={"expression": "rank(volume)", "category": "liquidity"},
        headers=auth_headers,
    )
    entry_id = create_resp.json()["entry"]["id"]

    patch_resp = await client.patch(
        f"/api/v1/factor-pool/{entry_id}",
        json={"pool_status": "rejected", "tags": ["low ic"], "category": "quality"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["pool_status"] == "rejected"
    assert patched["tags"] == ["category:quality", "low ic"]

    other_user = User(id=uuid.uuid4(), email="other-factor-pool@test.com", is_active=True)
    db_session.add(other_user)
    await db_session.commit()
    other_headers = {"Authorization": f"Bearer {create_access_token(other_user.id, other_user.email)}"}
    other_get = await client.get(f"/api/v1/factor-pool/{entry_id}", headers=other_headers)
    assert other_get.status_code == 404

    delete_resp = await client.delete(f"/api/v1/factor-pool/{entry_id}", headers=auth_headers)
    assert delete_resp.status_code == 204
    get_resp = await client.get(f"/api/v1/factor-pool/{entry_id}", headers=auth_headers)
    assert get_resp.status_code == 404


async def test_invalid_status_rejected(client, test_user, auth_headers):
    resp = await client.post(
        "/api/v1/factor-pool",
        json={"expression": "rank(open)", "pool_status": "candidate"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "Unknown factor pool status" in resp.json()["detail"]
