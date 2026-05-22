"""Experiment-ledger foundation regressions."""

import pytest
from sqlalchemy import select

from quantgpt.experiment_ledger import (
    ExperimentLedgerError,
    compute_factor_hash,
    get_experiment,
    list_experiments,
    record_experiment,
    record_experiment_artifact,
    record_experiment_result,
    record_export_event,
    record_promotion_event,
    summarize_trial_counts,
    transition_status,
)
from quantgpt.models import FactorRegistry


def test_factor_hash_is_stable_and_includes_scope():
    base = {
        "universe": "hs300",
        "market": "a_share",
        "asset_class": "equity",
        "data_snapshot_id": "snap_a",
        "holding_period": 5,
        "validation_stage": "selection",
    }

    h1 = compute_factor_hash("Rank( Close )", base)
    h2 = compute_factor_hash("rank(close)", dict(base))
    h3 = compute_factor_hash("rank(close)", {**base, "universe": "csi500"})
    h4 = compute_factor_hash("rank(close)", {**base, "data_snapshot_id": "snap_b"})

    assert h1 == h2
    assert h1.startswith("fh_")
    assert h3 != h1
    assert h4 != h1


@pytest.mark.asyncio
async def test_record_experiment_persists_hash_registry_and_query(db_session):
    experiment = await record_experiment(
        db_session,
        expression="rank(close)",
        params={
            "universe": "hs300",
            "market": "a_share",
            "data_snapshot_id": "snap_001",
            "holding_period": 5,
            "direction_policy": "train_fixed",
            "research_mode": "formal_selection",
        },
        status="parsed",
        created_by="mcp",
    )

    fetched = await get_experiment(db_session, experiment.experiment_id)
    listed = await list_experiments(db_session, status="parsed", universe="hs300")
    registry = (
        await db_session.execute(select(FactorRegistry).where(FactorRegistry.factor_hash == experiment.factor_hash))
    ).scalar_one()

    assert fetched is not None
    assert fetched.experiment_id == experiment.experiment_id
    assert fetched.expression_normalized == "rank(close)"
    assert fetched.data_snapshot_id == "snap_001"
    assert listed == [experiment]
    assert registry.first_experiment_id == experiment.experiment_id
    assert registry.latest_experiment_id == experiment.experiment_id


@pytest.mark.asyncio
async def test_record_failed_experiment_is_not_lost(db_session):
    experiment = await record_experiment(
        db_session,
        expression="bad_func(close)",
        params={"universe": "hs300"},
        status="parse_failed",
        failure_reason="unknown function: bad_func",
    )

    fetched = await get_experiment(db_session, experiment.experiment_id)

    assert fetched is not None
    assert fetched.status == "parse_failed"
    assert fetched.failure_reason == "unknown function: bad_func"


@pytest.mark.asyncio
async def test_status_transitions_reject_illegal_or_terminal_moves(db_session):
    experiment = await record_experiment(db_session, expression="rank(close)", params={}, status="draft")

    with pytest.raises(ExperimentLedgerError, match="Illegal experiment transition"):
        await transition_status(db_session, experiment.experiment_id, "candidate")

    await transition_status(db_session, experiment.experiment_id, "parsed")
    await transition_status(db_session, experiment.experiment_id, "validated_oos")
    await transition_status(db_session, experiment.experiment_id, "candidate")
    await transition_status(db_session, experiment.experiment_id, "exported")

    with pytest.raises(ExperimentLedgerError, match="terminal"):
        await transition_status(db_session, experiment.experiment_id, "archived")


@pytest.mark.asyncio
async def test_record_result_artifact_promotion_and_export_events(db_session):
    experiment = await record_experiment(db_session, expression="rank(close)", params={}, status="validated_oos")

    result = await record_experiment_result(
        db_session,
        experiment_id=experiment.experiment_id,
        stage="validated_oos",
        validation_stage="final",
        direction_policy="train_fixed",
        metrics={"sharpe": 1.2},
        oos_score={"decision": "candidate"},
        data_quality={"enabled": True},
    )
    artifact = await record_experiment_artifact(
        db_session,
        experiment_id=experiment.experiment_id,
        artifact_type="report",
        uri="reports/r.html",
        content_hash="sha256:abc",
        metadata={"format": "html"},
    )
    promotion = await record_promotion_event(
        db_session,
        experiment_id=experiment.experiment_id,
        boundary="export",
        decision="blocked",
        blockers=["DATA_SNAPSHOT_REQUIRED"],
    )
    export = await record_export_event(
        db_session,
        experiment_id=experiment.experiment_id,
        schema_version="strategy_signal.v1",
        export_path="exports/signal.json",
        payload={"schema_version": "strategy_signal.v1"},
    )

    assert result.metrics == {"sharpe": 1.2}
    assert artifact.artifact_metadata == {"format": "html"}
    assert promotion.blockers == ["DATA_SNAPSHOT_REQUIRED"]
    assert export.payload_hash is not None


@pytest.mark.asyncio
async def test_summarize_trial_counts_uses_experiment_rows(db_session):
    params = {"universe": "hs300", "data_snapshot_id": "snap_a"}
    first = await record_experiment(db_session, expression="rank(close)", params=params, status="validated_oos")
    await record_experiment(db_session, expression="rank(open)", params=params, status="rejected")
    await record_experiment(
        db_session,
        expression="rank(close)",
        params={**params, "universe": "csi500"},
        status="validated_oos",
    )

    counts = await summarize_trial_counts(db_session, universe="hs300", factor_hash=first.factor_hash)

    assert counts["total_trials_in_project"] == 3
    assert counts["trials_in_same_universe"] == 2
    assert counts["trials_by_factor_hash"] == 1
