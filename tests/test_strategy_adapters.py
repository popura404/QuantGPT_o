"""Strategy adapter contract tests."""

import ast
from pathlib import Path

import pytest

from quantgpt.strategy.adapters import get_adapter, list_data_fields, list_markets


def test_list_markets_returns_a_share_capabilities():
    markets = list_markets()

    a_share = next(market for market in markets if market["market"] == "a_share")
    assert a_share["asset_class"] == "equity"
    assert a_share["frequency"] == "daily"
    assert "hs300" in a_share["universes"]
    assert "hs300" in a_share["benchmarks"]
    assert a_share["supports_short"] is False


def test_list_data_fields_returns_local_expression_fields():
    fields = {field["name"] for field in list_data_fields("a_share")}

    assert {"open", "close", "volume", "vwap", "returns", "market_cap"}.issubset(fields)


def test_unknown_market_raises_clear_error():
    with pytest.raises(ValueError, match="Unsupported market"):
        get_adapter("crypto")


def test_strategy_core_modules_do_not_import_a_share_market_constants_directly():
    strategy_dir = Path("quantgpt/strategy")
    allowed = {strategy_dir / "a_share_adapter.py"}
    banned = {"VALID_UNIVERSES", "VALID_BENCHMARKS", "BENCHMARK_CODES", "UNIVERSES"}

    offenders = []
    for path in strategy_dir.glob("*.py"):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = {alias.name for alias in node.names}
                if banned & imported:
                    offenders.append(str(path))
    assert offenders == []
