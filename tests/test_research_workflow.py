"""Real HTTP/SQLite/frozen-panel workflow, without network or caller proofs."""

import pytest


@pytest.mark.asyncio
async def test_offline_evaluation_retry_strategy_lineage_and_export(client, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(tmp_path / "snapshots"))
    created = await client.post("/api/v1/research/projects", headers=auth_headers,
                                json={"name": "Offline contract", "market": "demo_global_equity"})
    assert created.status_code == 201, created.text
    base = f"/api/v1/research/projects/{created.json()['id']}"
    response = await client.post(base + "/offline-demo", headers=auth_headers)
    assert response.status_code == 200, response.text
    demo = response.json()
    assert demo["synthetic"] is True
    request = {"definitions": demo["definitions"], "config": demo["config"]}
    evaluated = await client.post(base + "/evaluations", headers=auth_headers, json=request)
    assert evaluated.status_code == 200, evaluated.text
    first = evaluated.json()["evaluations"][0]
    assert first["summary"]["performance_observed"] is True, first["failure_reason"]
    assert evaluated.json()["batch_diagnostics"] == {"panel_builds": 1, "engine_calls": 1}
    repeated = await client.post(base + "/evaluations", headers=auth_headers, json=request)
    assert repeated.json()["evaluations"][0]["evaluation_id"] == first["evaluation_id"]
    assert repeated.json()["batch_diagnostics"] == {"panel_builds": 0, "engine_calls": 0}
    artifact = first["summary"]["artifacts"][0]
    loaded = await client.get(base + "/artifacts/" + artifact["artifact_id"], headers=auth_headers)
    assert loaded.status_code == 200 and len(loaded.json()["payload"]) > 100
    reference = {key: first[key] for key in ("evaluation_id", "evaluation_hash", "definition_hash", "project_id", "backend")}
    reference["scope"] = "selection"
    spec = demo["strategy_template"]
    spec["factor_evaluations"] = [reference]
    run = await client.post(base + "/strategy-runs", headers=auth_headers, json={"spec": spec, "config": demo["config"]})
    assert run.status_code == 200, run.text
    result = run.json()
    assert result["spec"]["schema_version"] == "strategy_spec/v2"
    assert result["metrics"] and result["signal_ref"]
    signal = await client.get(base + "/artifacts/" + result["signal_ref"]["artifact_id"], headers=auth_headers)
    assert signal.json()["payload"][0]["factor_value"] is not None
    assert "score" in signal.json()["payload"][0]
    optimization = await client.post(base + "/portfolio-optimizations", headers=auth_headers,
        json={"signal_ref": result["signal_ref"], "config": {
            "asof_session": demo["config"]["window"]["end_session"], "max_asset_weight": .2}})
    assert optimization.status_code == 200, optimization.text
    assert optimization.json()["feasible"] is True
    assert optimization.json()["local_strategy_eligible"] is False
    missing_industry = await client.post(base + "/portfolio-optimizations", headers=auth_headers,
        json={"signal_ref": result["signal_ref"], "config": {
            "asof_session": demo["config"]["window"]["end_session"], "industry_max_weights": {"tech": .3}}})
    assert missing_industry.status_code == 200, missing_industry.text
    assert missing_industry.json()["feasible"] is False
    export = await client.post(base + "/strategy-runs/" + result["strategy_run_id"] + "/export", headers=auth_headers)
    assert export.status_code == 200
    assert export.json()["exported"] is False
    assert "FINAL_TEST_REQUIRED" in export.json()["validation"]["blockers"]
    spec["factors"][0]["direction"] = "lower_is_better"
    forged = await client.post(base + "/strategy-runs", headers=auth_headers, json={"spec": spec, "config": demo["config"]})
    assert forged.status_code == 422 and "MISMATCH" in forged.text


def test_missing_trial_counts_and_serial_bootstrap():
    from quantgpt.statistics.multiple_testing import bootstrap_mean_ci, multiple_testing_report

    assert multiple_testing_report(p_value=.001, trial_counts={})["passed"] is False
    values = [-.01] * 40 + [.01] * 40
    iid = bootstrap_mean_ci(values, seed=7, block_length=1)
    blocks = bootstrap_mean_ci(values, seed=7, block_length=10)
    assert blocks["method"] == "circular_moving_block_bootstrap"
    assert blocks["upper"] - blocks["lower"] > iid["upper"] - iid["lower"]
