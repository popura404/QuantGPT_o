"""Post-MVP strategy persistence tests."""

import pytest

from quantgpt.strategy.spec import example_strategy_spec_v1

pytestmark = pytest.mark.asyncio


async def test_strategy_spec_and_run_persistence(client, auth_headers):
    spec = example_strategy_spec_v1()
    spec["universe"] = "small_scale"

    created = await client.post(
        "/api/v1/strategy/specs",
        headers=auth_headers,
        json={"spec": spec, "tags": ["post-mvp"]},
    )
    assert created.status_code == 201
    strategy = created.json()
    assert strategy["schema_version"] == "strategy_spec/v1"
    assert strategy["tags"] == ["post-mvp"]

    listed = await client.get("/api/v1/strategy/specs", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["strategies"][0]["id"] == strategy["id"]

    run = await client.post(
        "/api/v1/strategy/runs",
        headers=auth_headers,
        json={
            "strategy_id": strategy["id"],
            "result": {"strategy_score": {"score": 80}, "summary_json": "/tmp/summary.json"},
            "signal_export": {"signals": []},
        },
    )
    assert run.status_code == 201
    assert run.json()["strategy_id"] == strategy["id"]
    assert run.json()["signal_export"] == {"signals": []}

    runs = await client.get(f"/api/v1/strategy/runs?strategy_id={strategy['id']}", headers=auth_headers)
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["id"] == run.json()["id"]
