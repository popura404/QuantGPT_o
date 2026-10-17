"""Numeric/missing-mask parity against the real compiled PyO3 extension.

Set QUANTGPT_REQUIRE_RUST=1 in the Rust CI job so missing binaries fail rather
than silently skip. Passing this subset suite never enables the global gate.
"""

import importlib
import inspect
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantgpt import rust_bridge
from quantgpt.expression_parser import parse_expression

GOLDEN = json.loads((Path(__file__).parent / "fixtures/research/operator_golden.json").read_text(encoding="utf-8"))
EXPRESSIONS = (
    "close", "-close", "rank(close)", "scale(close)", "ts_rank(close, 3)",
    "ts_shift(close, 2)", "ts_delta(close, 3)", "rank(ts_delta(close, 2))",
    "scale(ts_rank(close, 4))", "rank(close / open)", "close + open", "close - open",
    "close * volume", "close / open", "((close + open) * volume) / (volume + 1)",
)


@pytest.fixture(scope="module")
def compiled_engine():
    try:
        engine = importlib.import_module("quantgpt_engine")
    except ImportError:
        if os.environ.get("QUANTGPT_REQUIRE_RUST") == "1":
            pytest.fail("Rust differential suite requires an installed compiled extension")
        pytest.skip("compiled Rust extension is not installed")
    assert inspect.isbuiltin(engine.eval_expression), "A Python stub cannot certify numerical parity"
    return engine


@pytest.fixture
def native(compiled_engine, monkeypatch):
    monkeypatch.setattr(rust_bridge, "_engine", compiled_engine)
    monkeypatch.setattr(rust_bridge, "RUST_ENABLED", True)

    def evaluate(frame, expression):
        assert rust_bridge.rust_expression_supported(expression), expression
        result = rust_bridge.eval_factor_expression_with_metadata(frame, expression, trusted=False)
        assert result.engine_used == "rust", result.fallback_reason
        assert result.fallback_reason is None
        assert not result.contract_verified
        return result.values

    return evaluate


def _assert_parity(native, frame, expression):
    actual = native(frame, expression)
    expected = parse_expression(expression)(frame)
    assert actual.index.equals(expected.index)
    np.testing.assert_array_equal(np.isnan(actual.to_numpy()), np.isnan(expected.to_numpy()))
    np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy(), rtol=1e-12, atol=1e-12, equal_nan=True)
    return actual


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["id"])
def test_real_extension_passes_shared_hand_calculated_cases(native, case):
    frame = pd.DataFrame(case["rows"])
    frame["close"] = pd.to_numeric(frame["close"])
    actual = _assert_parity(native, frame, case["expression"])
    np.testing.assert_allclose(actual, np.asarray(case["expected"], dtype=float), rtol=1e-12, atol=1e-12, equal_nan=True)


def _random_panel(seed):
    rng = np.random.default_rng(seed)
    size = 6 * 30
    frame = pd.DataFrame({
        "stock_code": np.repeat([f"S{i}" for i in range(6)], 30),
        "trade_date": np.tile(pd.bdate_range("2024-01-02", periods=30), 6),
        "close": rng.integers(-5, 6, size).astype(float),
        "open": rng.integers(-3, 4, size).astype(float),
        "volume": rng.integers(0, 10, size).astype(float),
    })
    frame.loc[rng.choice(size, 24, replace=False), "close"] = np.nan
    frame.loc[rng.choice(size, 3, replace=False), "close"] = np.inf
    frame.loc[rng.choice(size, 3, replace=False), "close"] = -np.inf
    frame.loc[rng.choice(size, 8, replace=False), "open"] = .0000000000000001
    frame.index = np.arange(size) // 2  # Deliberately nonunique caller indexes.
    return frame.sample(frac=1, random_state=seed)


@pytest.mark.parametrize("seed", [7, 42, 20261018])
@pytest.mark.parametrize("expression", EXPRESSIONS)
def test_real_extension_random_panels_match_values_and_nan_masks(native, seed, expression):
    _assert_parity(native, _random_panel(seed), expression)


@pytest.mark.parametrize("expression", ["rank(close)", "scale(close)", "ts_rank(close, 3)", "ts_shift(close, 1)"])
def test_keyless_and_stock_only_frames_keep_python_semantics(native, expression):
    frame = pd.DataFrame({"close": [1., np.nan, 1., 2., -1., 0.]})
    _assert_parity(native, frame, expression)
    frame["stock_code"] = ["B", "A", "B", "A", "A", "B"]
    _assert_parity(native, frame, expression)
    _assert_parity(native, frame.iloc[:0], expression)


@pytest.mark.parametrize("expression", ["rank(close)", "scale(close)", "ts_rank(close, 3)"])
def test_appending_future_rows_does_not_change_native_history(native, expression):
    frame = _random_panel(42).reset_index(drop=True)
    before = native(frame, expression)
    future = frame.iloc[:6].copy()
    future["trade_date"] = pd.Timestamp("2025-01-01")
    future["stock_code"] = [f"S{i}" for i in range(6)]
    future["close"] = np.arange(6) * 1000000
    combined = pd.concat([frame, future], ignore_index=True)
    after = _assert_parity(native, combined, expression)
    np.testing.assert_allclose(before, after.iloc[:len(frame)], rtol=1e-12, atol=1e-12, equal_nan=True)


def test_native_fractional_window_is_rejected(compiled_engine):
    with pytest.raises(ValueError, match="integer"):
        compiled_engine.eval_expression("ts_rank(close, 2.5)", {"close": np.array([1., 2., 3.])}, [], [])


def test_nullable_floating_inputs_preserve_missing_mask(native):
    frame = pd.DataFrame({"close": pd.Series([1., None, 1., 3.], dtype="Float64")})
    _assert_parity(native, frame, "rank(close)")


def test_real_subset_obeys_explicit_build_gate_without_enabling_it_globally(compiled_engine, monkeypatch):
    frame = pd.DataFrame({"close": [1., 1., 3.]})
    monkeypatch.setattr(rust_bridge, "_engine", compiled_engine)
    monkeypatch.setattr(rust_bridge, "RUST_ENABLED", True)
    assert rust_bridge.RUST_VERIFIED_SEMANTICS_VERSION is None
    default = rust_bridge.eval_factor_expression_with_metadata(frame, "rank(close)")
    assert default.engine_used == "python"
    assert default.fallback_reason == "rust_semantics_unverified"
    monkeypatch.setattr(rust_bridge, "RUST_VERIFIED_SEMANTICS_VERSION", rust_bridge.OPERATOR_SEMANTICS_VERSION)
    enabled = rust_bridge.eval_factor_expression_with_metadata(frame, "rank(close)")
    assert enabled.engine_used == "rust"
    assert enabled.contract_verified
    np.testing.assert_allclose(enabled.values, [.5, .5, 1.])
