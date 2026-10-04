"""Economic and row-identity invariants for factor_semantics/v2."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantgpt.expression_parser import infer_expression_lookback, parse_expression
from quantgpt.neutralize import neutralize_factor


def _panel():
    return pd.DataFrame({
        "stock_code": ["A", "A", "B", "B"],
        "trade_date": pd.to_datetime(["2024-01-01", "2024-01-02"] * 2),
        "close": [10.0, 20.0, 30.0, 40.0],
        "volume": [100.0] * 4,
        "industry": ["tech"] * 4,
    }, index=[10, 11, 20, 21])


def test_scale_future_append_does_not_change_history():
    original = _panel()
    future = original.iloc[[1, 3]].copy()
    future["trade_date"] = pd.Timestamp("2024-01-03")
    future["close"] = [100.0, 200.0]
    future.index = [12, 22]
    fn = parse_expression("scale(close)")
    pd.testing.assert_series_equal(fn(original), fn(pd.concat([original, future])).loc[original.index])
    np.testing.assert_allclose(fn(original), [0, 0, 1, 1])


def test_boll_does_not_cross_security_boundaries():
    np.testing.assert_allclose(parse_expression("boll_mid(close, 2)")(_panel()), [10, 15, 30, 35])


def test_neutralization_keeps_stock_sorted_row_identity():
    df = _panel()
    values = pd.Series([1.0, 2.0, 3.0, 8.0], index=df.index)
    result = neutralize_factor(values, df, industry=True)
    pd.testing.assert_series_equal(result, pd.Series([-1.0, -3.0, 1.0, 3.0], index=df.index))


@pytest.mark.parametrize("expression", ["cap", "market_cap"])
def test_missing_cap_is_not_replaced_by_price_or_turnover(expression):
    with pytest.raises(ValueError, match="market_cap"):
        parse_expression(expression)(_panel())


def test_cap_neutralization_requires_real_cap():
    df = _panel()
    with pytest.raises(ValueError, match="market_cap"):
        neutralize_factor(df["close"], df, market_cap=True)


def test_industry_neutralization_requires_supplied_classifications():
    df = _panel().drop(columns="industry")
    with pytest.raises(ValueError, match="industry"):
        neutralize_factor(df["close"], df, industry=True)


@pytest.mark.parametrize("expression", [
    "ts_mean(close, 2)", "ts_delta(close, 1)", "returns", "boll_mid(close, 2)",
    "ts_corr(close, volume, 2)", "atr(2)", "adv2", "trade_when(close > 15, close, 0)",
])
def test_shuffled_rows_have_identical_keyed_results(expression):
    original = _panel()
    shuffled = original.iloc[[3, 0, 2, 1]]
    fn = parse_expression(expression)
    pd.testing.assert_series_equal(fn(original), fn(shuffled).reindex(original.index))


def test_stable_security_and_session_keys_take_precedence():
    df = _panel().rename(columns={"stock_code": "security_id", "trade_date": "session"})
    np.testing.assert_allclose(parse_expression("boll_mid(close, 2)")(df), [10, 15, 30, 35])
    np.testing.assert_allclose(parse_expression("scale(close)")(df), [0, 0, 1, 1])


def test_scale_and_rank_define_ties_and_nonfinite_values():
    df = pd.DataFrame({"trade_date": ["2024-01-01"] * 5, "close": [0, 0, np.nan, np.inf, -np.inf]})
    np.testing.assert_allclose(parse_expression("scale(close)")(df), [0, 0, np.nan, np.nan, np.nan])
    np.testing.assert_allclose(parse_expression("rank(close)")(df), [.75, .75, np.nan, np.nan, np.nan])


def test_duplicate_security_session_identity_is_rejected():
    df = pd.concat([_panel(), _panel().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        parse_expression("close")(df)


_GOLDEN = json.loads((Path(__file__).parent / "fixtures/research/operator_golden.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _GOLDEN["cases"], ids=lambda case: case["id"])
def test_shared_operator_golden_cases(case):
    df = pd.DataFrame(case["rows"])
    df["close"] = pd.to_numeric(df["close"])
    result = parse_expression(case["expression"])(df)
    expected = np.asarray(case["expected"], dtype=float)
    np.testing.assert_allclose(result, expected, rtol=1e-12, atol=1e-12, equal_nan=True)
    assert result.attrs["semantics_version"] == _GOLDEN["semantics_version"]


@pytest.mark.parametrize("case", _GOLDEN["warmup_cases"], ids=lambda case: case["expression"])
def test_shared_ast_lookback_golden_cases(case):
    result = infer_expression_lookback(case["expression"])
    assert result.required_observations == case["required_observations"]
    assert result.prior_sessions == case["prior_sessions"]
    assert not result.recursive


def test_oos_warmup_composes_nested_windows():
    from quantgpt.validation.split import infer_warmup_days

    assert infer_warmup_days("ts_mean(ts_mean(close, 20), 60)", 5) == (79, [])
    assert infer_expression_lookback("ts_mean(close, 20) * 1000").required_observations == 20
    result = infer_expression_lookback("ema(close, 20)")
    assert result.recursive
    assert result.warnings


def test_neutralization_aligns_factor_index_and_supplied_cap():
    dates = pd.date_range("2024-01-01", periods=2)
    df = pd.DataFrame({
        "security_id": list("ABCDEF") * 2,
        "session": np.repeat(dates, 6),
        "market_cap": np.tile(np.exp(np.arange(1, 7)), 2),
    }, index=np.arange(100, 112))
    factor = pd.Series(2 * np.log(df["market_cap"]) + 5, index=df.index).iloc[::-1]
    result = neutralize_factor(factor, df.sample(frac=1, random_state=42), market_cap=True)
    assert result.index.equals(factor.index)
    np.testing.assert_allclose(result, 0, atol=1e-12)


def test_missing_return_is_not_forward_filled():
    df = _panel().iloc[:2].copy()
    df.iloc[1, df.columns.get_loc("close")] = np.nan
    assert parse_expression("returns")(df).isna().all()
