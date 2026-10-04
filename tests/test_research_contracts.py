"""Research contract validation; does not certify a provider or a simulation."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from quantgpt.research.contracts import (
    EVENT_ORDER,
    SEMANTICS_VERSION,
    US_RESEARCH_FIELDS,
    ArtifactRef,
    CapabilityBlockerError,
    CapabilityReport,
    EngineIdentity,
    EvaluationConfigV1,
    EvaluationRef,
    FactorDefinitionV1,
    PrincipalContext,
    ResearchStatus,
    Session,
    SignalRef,
    SimulationConfigV1,
    authorize_reference,
    canonical_json,
    definition_hash,
    evaluation_hash,
    normalize_expression,
    strategy_hash,
    validate_task_transition,
)
from quantgpt.strategy.spec import example_strategy_spec, example_strategy_spec_v1, parse_strategy_spec

FIXTURES = Path(__file__).parent / "fixtures" / "research"
DIGEST = "a" * 64


@pytest.fixture
def definition():
    return FactorDefinitionV1(expression="rank(close)", fields=(US_RESEARCH_FIELDS[2],))


@pytest.fixture
def config_payload():
    return {
        "backend": "local",
        "scope": {
            "market": "us_equity", "currency": "USD", "universe_id": "fixture_cohort",
            "universe_mode": "fixed_cohort", "universe_version": "fixture/v1",
            "calendar_id": "synthetic", "calendar_version": "v1", "timezone": "America/New_York",
            "benchmark": "synthetic_total_return",
        },
        "window": {
            "start_session": "2024-01-03", "end_session": "2024-01-31",
            "split_id": "fixture_split", "split_hash": DIGEST,
        },
        "direction": "higher_is_better",
        "simulation_config": {"rebalance_anchor_session": "2024-01-02"},
        "data_inputs": [{
            "manifest_id": "fixture-manifest", "content_sha256": DIGEST,
            "version_status": "verified", "source": "synthetic", "feed": "fixture",
            "adjustment": "raw", "asof": "2024-02-01T00:00:00Z",
        }],
        "engine": {"engine": "python", "version": "fixture/v1", "code_version": "fixture", "conformance": "verified"},
    }


def test_definition_hash_normalizes_spacing_and_field_order_but_keeps_economics(definition):
    spaced = definition.model_copy(update={"expression": " rank( close ) "})
    assert definition_hash(definition) == definition_hash(spaced)
    multiple = definition.model_copy(update={"fields": (US_RESEARCH_FIELDS[2], US_RESEARCH_FIELDS[3])})
    reversed_fields = multiple.model_copy(update={"fields": tuple(reversed(multiple.fields))})
    assert definition_hash(multiple) == definition_hash(reversed_fields)
    changed_field = definition.fields[0].model_copy(update={"unit": "cents/share"})
    assert definition_hash(definition) != definition_hash(definition.model_copy(update={"fields": (changed_field,)}))
    assert definition_hash(definition) != definition_hash(definition.model_copy(update={"semantics_version": "legacy/v1"}))
    assert normalize_expression("foo bar") != normalize_expression("foobar")
    assert normalize_expression("x >= 1") != normalize_expression("x > = 1")


@pytest.mark.parametrize("change", ["fees", "direction", "window", "data", "engine", "split", "vintage", "start_state"])
def test_evaluation_hash_covers_all_research_inputs(definition, config_payload, change):
    original = EvaluationConfigV1.model_validate(config_payload)
    changed = deepcopy(config_payload)
    if change == "fees":
        changed["simulation_config"]["fees_bps"] = 5
    elif change == "direction":
        changed["direction"] = "lower_is_better"
    elif change == "window":
        changed["window"]["end_session"] = "2024-02-01"
    elif change == "data":
        changed["data_inputs"][0]["content_sha256"] = "b" * 64
    elif change == "engine":
        changed["engine"]["version"] = "fixture/v2"
    elif change == "split":
        changed["window"]["split_hash"] = "b" * 64
    elif change == "vintage":
        changed["data_inputs"][0]["asof"] = "2024-03-01T00:00:00Z"
    else:
        changed["simulation_config"] = {
            "rebalance_anchor_session": "2024-01-02", "evaluation_start_state": "carry_forward",
            "initial_cash": 100_000, "starting_state_session": "2024-01-02",
            "starting_state_ref": {"artifact_id": "initial", "project_id": "p1", "kind": "starting_state", "content_sha256": DIGEST},
        }
    assert evaluation_hash(definition, original) != evaluation_hash(definition, EvaluationConfigV1.model_validate(changed))
    assert evaluation_hash(definition, original) == evaluation_hash(definition, EvaluationConfigV1.model_validate(original.model_dump(mode="json")))


def test_strategy_hash_covers_spec_and_lineage(config_payload):
    config = EvaluationConfigV1.model_validate(config_payload)
    ref = EvaluationRef(evaluation_id="e1", project_id="p1", evaluation_hash=DIGEST, definition_hash=DIGEST, backend="local", scope="selection")
    first = strategy_hash({"weight": 1}, config, [ref])
    assert first != strategy_hash({"weight": 0.5}, config, [ref])
    assert first != strategy_hash({"weight": 1}, config, [ref.model_copy(update={"evaluation_id": "e2"})])
    with pytest.raises(ValueError, match="Forbidden"):
        strategy_hash({"nested": {"broker": "not_allowed"}}, config, [ref])


def test_hash_rejects_unknown_numerical_values_and_semantics_mismatch(definition, config_payload):
    with pytest.raises(ValueError):
        canonical_json({"score": float("nan")})
    config_payload["engine"]["semantics_version"] = "different/v1"
    with pytest.raises(ValueError, match="semantics_version"):
        evaluation_hash(definition, EvaluationConfigV1.model_validate(config_payload))


@pytest.mark.parametrize("payload", [
    {"evaluation_start_state": "fresh_cash", "initial_positions": [{"security_id": "A", "quantity": 1, "previous_close": 100}]},
    {"evaluation_start_state": "carry_forward"},
    {"initial_cash": 0},
    {"initial_cash": float("inf")},
    {"fill_time": "same_session_close"},
    {"allow_short": True},
    {"allow_leverage": True},
    {"event_order": list(reversed(EVENT_ORDER))},
    {"broker": "not_allowed"},
])
def test_invalid_or_ambiguous_simulation_is_rejected(payload):
    with pytest.raises(ValidationError):
        SimulationConfigV1.model_validate({"rebalance_anchor_session": "2024-01-02", **payload})


def test_v0_v1_remain_readable_and_do_not_accept_new_simulation_payload():
    for example in (example_strategy_spec(), example_strategy_spec_v1()):
        assert parse_strategy_spec(example).schema_version == example["schema_version"]
        example["simulation_config"] = {"rebalance_anchor_session": "2024-01-02"}
        with pytest.raises(ValidationError):
            parse_strategy_spec(example)


def test_data_and_engine_unknown_do_not_become_validated_evidence(config_payload):
    config_payload["data_inputs"][0].update(version_status="unknown", manifest_id=None, content_sha256=None)
    assert EvaluationConfigV1.model_validate(config_payload).evidence_eligibility == "research_only"
    config_payload["data_inputs"][0]["version_status"] = "verified"
    with pytest.raises(ValidationError, match="manifest_id"):
        EvaluationConfigV1.model_validate(config_payload)


def test_remote_scope_never_claims_local_final(config_payload):
    config_payload.update(backend="wq", simulation_config=None, remote_settings={"region": "USA"})
    config_payload["window"]["phase"] = "platform"
    config_payload["engine"] = {"engine": "wq", "version": "unknown", "code_version": "fixture", "semantics_version": "wq_platform/unknown"}
    config_payload["data_inputs"][0].update(version_status="unknown", manifest_id=None, content_sha256=None)
    assert EvaluationConfigV1.model_validate(config_payload).evidence_eligibility == "platform_only"
    config_payload["window"]["phase"] = "final"
    with pytest.raises(ValidationError, match="WQ evaluations"):
        EvaluationConfigV1.model_validate(config_payload)


def test_purge_covers_real_horizon(config_payload):
    config_payload["window"]["label_horizon_sessions"] = 5
    with pytest.raises(ValidationError, match="purge_sessions"):
        EvaluationConfigV1.model_validate(config_payload)


def test_missing_neutralization_fields_fail_with_structured_blockers():
    report = CapabilityReport(backend="fixture", fields={"close": "available", "volume": "available", "market_cap": "unavailable"})
    with pytest.raises(CapabilityBlockerError) as caught:
        report.require_neutralization(industry=True, market_cap=True)
    assert {item.error_code for item in caught.value.blockers} == {"CAPABILITY_FIELD_UNKNOWN", "CAPABILITY_FIELD_UNAVAILABLE"}
    assert {name for item in caught.value.blockers for name in item.affected_fields} == {"industry", "market_cap"}
    assert all(not item.retryable and item.next_action for item in caught.value.blockers)


def test_current_membership_is_rechecked_on_each_reference_read():
    class Authorizer:
        enabled = True
        calls = 0

        def require_current_membership(self, *, actor_id, project_id, permission):
            self.calls += 1
            assert (actor_id, project_id, permission) == ("actor", "p1", "read")
            if not self.enabled:
                raise PermissionError("PROJECT_MEMBERSHIP_REVOKED")

    principal = PrincipalContext(actor_id="actor", project_id="p1", transport="http", resolved_from="authenticated_credentials")
    ref = ArtifactRef(artifact_id="a1", project_id="p1", kind="signal", content_sha256=DIGEST)
    authorizer = Authorizer()
    authorize_reference(principal, ref, authorizer)
    authorizer.enabled = False
    with pytest.raises(PermissionError, match="REVOKED"):
        authorize_reference(principal, ref, authorizer)
    assert authorizer.calls == 2
    with pytest.raises(PermissionError, match="SCOPE_MISMATCH"):
        authorize_reference(principal, ref.model_copy(update={"project_id": "p2"}), authorizer)


def test_principal_cannot_be_inferred_from_project_selector():
    with pytest.raises(ValidationError):
        PrincipalContext.model_validate({"project_id": "p1"})
    with pytest.raises(ValidationError, match="transport"):
        PrincipalContext(actor_id="actor", project_id="p1", transport="http", resolved_from="server_connection_config")


def test_signal_ref_enforces_project_scope_and_aware_availability():
    ref = {
        "artifact_ref": {"artifact_id": "a1", "project_id": "p1", "kind": "signal", "content_sha256": DIGEST},
        "evaluation_ref": {"evaluation_id": "e1", "project_id": "p1", "evaluation_hash": DIGEST, "definition_hash": DIGEST, "backend": "local", "scope": "selection"},
        "session": "2024-01-02", "available_at": "2024-01-02T21:00:01Z",
    }
    assert SignalRef.model_validate(ref).evaluation_ref.evaluation_id == "e1"
    ref["artifact_ref"]["project_id"] = "p2"
    with pytest.raises(ValidationError, match="project mismatch"):
        SignalRef.model_validate(ref)
    ref["artifact_ref"]["project_id"] = "p1"
    ref["available_at"] = "2024-01-02T21:00:01"
    with pytest.raises(ValidationError, match="timezone-aware"):
        SignalRef.model_validate(ref)


def test_session_contract_requires_real_instants_and_order():
    session = Session(session="2024-11-29", open_at="2024-11-29T09:30:00-05:00", close_at="2024-11-29T13:00:00-05:00")
    assert session.close_at.hour == 13  # This checks DTO support, not a real calendar provider.
    with pytest.raises(ValidationError, match="timezone-aware"):
        Session(session="2024-11-29", open_at=datetime(2024, 11, 29, 9, 30), close_at=datetime(2024, 11, 29, 13))


@pytest.mark.parametrize("previous,next_state", [
    ("completed", "running"), ("cancel_requested", "completed"), ("cancelled", "completed"),
    ("remote_outcome_unknown", "queued"), ("reconciliation_required", "queued"),
])
def test_terminal_and_unknown_remote_states_cannot_restart_blindly(previous, next_state):
    with pytest.raises(ValueError, match="INVALID_TASK_TRANSITION"):
        validate_task_transition(previous, next_state)


def test_contract_external_and_research_status_are_independent():
    status = ResearchStatus(contract_verified=True)
    assert status.external_integration == "not_run"
    assert status.research_decision == "not_evaluated"
    assert EngineIdentity(engine="python", version="fixture", code_version="fixture").conformance == "unverified"
    assert SEMANTICS_VERSION == "factor_semantics/v2"
