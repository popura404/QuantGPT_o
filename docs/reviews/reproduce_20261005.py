"""Read-only numerical reproductions for the 2026-10-05 repository audit.

Run from the repository root with Python, numpy, pandas, and SQLAlchemy.
Some pure functions are loaded from their original AST to avoid importing the
web server and optional market-data clients. No product functions are modified.
This is an audit demonstrator, not the project's regression test suite.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
import sys
import types

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quantgpt.data_snapshots import build_market_frame_snapshot
from quantgpt.expression_parser import parse_expression
from quantgpt.neutralize import neutralize_factor

namespace = {"pd": pd, "np": np, "dataclass": dataclasses.dataclass}


def load_functions(relative_path, names):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    nodes.extend(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    )
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"), namespace)


load_functions("quantgpt/backtest.py", {"build_rebalance_dates", "_calc_max_drawdown"})
load_functions("quantgpt/strategy/backtest.py", {"_calculate_strategy_returns"})
load_functions("quantgpt/strategy/risk.py", {"RiskApplicationResult", "apply_risk_rules", "_turnover", "_date_str"})

findings = []


def record(name, observed, expected, reproduced):
    findings.append({"finding": name, "observed": observed, "expected": expected, "reproduced": bool(reproduced)})


future = pd.DataFrame({"stock_code": ["A"] * 3, "trade_date": pd.date_range("2024-01-01", periods=3), "close": [10., 20., 40.]})
before = parse_expression("scale(close)")(future.iloc[:2]).tolist()
after = parse_expression("scale(close)")(future).iloc[:2].tolist()
record("scale_future_invariance", {"before": before, "after_appending_future": after}, "identical historical values", not np.allclose(before, after))

frame = pd.DataFrame({
    "stock_code": ["A", "A", "B", "B"],
    "trade_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-02"]),
    "close": [10., 20., 30., 40.], "volume": [100.] * 4,
    "factor_value": [1., 2., 3., 4.], "market_cap": [100., 200., 300., 400.],
})
observed = neutralize_factor(frame.factor_value, frame, market_cap=True).tolist()
record("neutralization_index_alignment", observed, [1., 2., 3., 4.], not np.allclose(observed, [1., 2., 3., 4.]))
observed = parse_expression("boll_mid(close,2)")(frame).tolist()
record("bollinger_cross_stock_contamination", observed, [10., 15., 30., 35.], not np.allclose(observed, [10., 15., 30., 35.]))

original = frame.assign(suspended=False)
changed = original.copy()
changed.loc[0, "suspended"] = True
snapshot_equal = build_market_frame_snapshot(original)["snapshot_id"] == build_market_frame_snapshot(changed)["snapshot_id"]
record("snapshot_ignores_tradability_values", snapshot_equal, False, snapshot_equal)

dates = pd.bdate_range("2024-01-01", periods=12)
full = namespace["build_rebalance_dates"](dates, 5, "2024-01-01")
sliced = namespace["build_rebalance_dates"](dates[2:], 5, "2024-01-01")
expected_dates = [str(value)[:10] for value in full if value >= dates[2]]
actual_dates = [str(value)[:10] for value in sliced]
record("rebalance_anchor_slice_invariance", actual_dates, expected_dates, actual_dates != expected_dates)

drawdown = float(namespace["_calc_max_drawdown"](pd.Series([-.1, 0.])))
record("initial_loss_drawdown", drawdown, -.1, not np.isclose(drawdown, -.1))

weights = pd.DataFrame({"trade_date": [dates[0], dates[0]], "stock_code": ["A", "B"], "target_weight": [.5, .5]})
returns_frame = pd.DataFrame({"trade_date": [dates[1], dates[1], dates[2], dates[2]], "stock_code": ["A", "B", "A", "B"], "daily_ret": [1., 0., -.5, 0.]})
returns, _ = namespace["_calculate_strategy_returns"](returns_frame, weights, pd.DataFrame(), 0)
cumulative = float((1 + returns).prod() - 1)
record("holdings_drift_between_rebalances", cumulative, 0., not np.isclose(cumulative, 0.))

spec = types.SimpleNamespace(risk_rules=types.SimpleNamespace(allow_short=False, max_asset_weight=1., max_turnover=None))
risk = namespace["apply_risk_rules"](weights, spec)
_, costs = namespace["_calculate_strategy_returns"](returns_frame, weights, risk.turnover_by_rebalance, 100)
record("initial_entry_cost", {"turnover": risk.turnover_by_rebalance.to_dict("records"), "cost_rows": len(costs)}, "nonzero initial entry cost for a funded-from-cash portfolio", len(costs) == 0)

print(json.dumps({"method": "synthetic inputs; original source functions; no network", "findings": findings}, ensure_ascii=False, indent=2, default=str))
