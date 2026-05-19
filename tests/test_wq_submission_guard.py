"""Tests for local WQ submission preflight enforcement."""

from quantgpt.wq_brain_service import run_batch_simulation, run_single_simulation, run_submit_by_ids
from quantgpt.wq_submission_guard import require_submission_preflight


class _FakeWQClient:
    def __init__(self):
        self.submit_calls = 0

    def simulate(self, *args, **kwargs):
        return {
            "ok": True,
            "alpha_id": "alpha-1",
            "is": {
                "sharpe": 1.5,
                "fitness": 1.2,
                "returns": 0.1,
                "turnover": 0.2,
            },
            "oos": {},
            "settings": {},
            "simulation_id": "sim-1",
        }

    def submit_alpha(self, alpha_id):
        self.submit_calls += 1
        return {
            "ok": True,
            "detail": "submitted",
            "platform_status": "ACTIVE",
            "status_code": 200,
        }


def _failed_preflight(_expression: str) -> dict:
    return {
        "allowed": False,
        "status": "failed",
        "error_code": "LOCAL_PREFLIGHT_FAILED",
        "decision": "reject",
    }


def _passed_preflight(_expression: str) -> dict:
    return {
        "allowed": True,
        "status": "passed",
        "decision": "candidate",
        "score": 80,
    }


def test_require_submission_preflight_needs_provenance_or_override():
    blocked = require_submission_preflight(None)
    waived = require_submission_preflight(None, override_reason="WQ-only expression; remote-only validation")

    assert blocked["allowed"] is False
    assert blocked["status"] == "unavailable"
    assert waived["allowed"] is True
    assert waived["status"] == "waived"
    assert waived["original_status"] == "unavailable"


def test_auto_submit_is_blocked_when_local_preflight_fails():
    client = _FakeWQClient()

    result = run_single_simulation(
        client,
        "rank(close)",
        auto_submit=True,
        submission_preflight_runner=_failed_preflight,
    )

    assert result["ok"] is True
    assert result["submitted"] is False
    assert result["submission_blocked"] is True
    assert result["submission_preflight"]["error_code"] == "LOCAL_PREFLIGHT_FAILED"
    assert client.submit_calls == 0


def test_auto_submit_can_be_waived_with_explicit_override_reason():
    client = _FakeWQClient()

    result = run_single_simulation(
        client,
        "rank(close)",
        auto_submit=True,
        submission_override_reason="WQ-only expression; remote OOS accepted",
        submission_preflight_runner=_failed_preflight,
    )

    assert result["submitted"] is True
    assert result["submission_preflight"]["status"] == "waived"
    assert result["submission_preflight"]["override_reason"]
    assert client.submit_calls == 1


def test_batch_auto_submit_is_blocked_by_local_preflight():
    client = _FakeWQClient()

    result = run_batch_simulation(
        client,
        "rank(close)",
        regions=["USA"],
        delays=[1],
        universes=["TOP3000"],
        neutralizations=["SUBINDUSTRY"],
        auto_submit=True,
        submission_preflight_runner=_failed_preflight,
    )

    sub_result = result["sub_results"]["USA_D1_TOP3000_SUBINDUSTRY"]
    assert result["ok"] is True
    assert sub_result["submitted"] is False
    assert sub_result["submission_blocked"] is True
    assert sub_result["submission_preflight"]["error_code"] == "LOCAL_PREFLIGHT_FAILED"
    assert client.submit_calls == 0


def test_submit_by_ids_requires_preflight_lookup_or_blocks():
    client = _FakeWQClient()

    result = run_submit_by_ids(client, ["alpha-1"])

    assert result["active"] == 0
    assert result["local_preflight_blocked"] == 1
    assert result["results"]["alpha-1"]["final_status"] == "LOCAL_PREFLIGHT_BLOCKED"
    assert client.submit_calls == 0


def test_submit_by_ids_submits_when_lookup_passes():
    client = _FakeWQClient()

    result = run_submit_by_ids(
        client,
        ["alpha-1"],
        submission_preflight_lookup=lambda _alpha_id: _passed_preflight("rank(close)"),
    )

    assert result["active"] == 1
    assert result["local_preflight_blocked"] == 0
    assert result["results"]["alpha-1"]["submission_preflight"]["status"] == "passed"
    assert client.submit_calls == 1
