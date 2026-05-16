"""Strategy templates and governance metadata for Post-MVP workflows."""

from __future__ import annotations

from copy import deepcopy

from .spec import example_strategy_spec_v1


def list_strategy_templates() -> list[dict]:
    return [
        _template_summary(_momentum_top_n_template()),
        _template_summary(_low_volume_reversal_template()),
    ]


def get_strategy_template(template_id: str) -> dict:
    templates = {
        "momentum_top_n_v1": _momentum_top_n_template,
        "low_volume_reversal_v1": _low_volume_reversal_template,
    }
    try:
        return templates[template_id]()
    except KeyError as exc:
        raise ValueError(f"Unknown strategy template: {template_id}") from exc


def instantiate_strategy_template(template_id: str, overrides: dict | None = None) -> dict:
    template = get_strategy_template(template_id)
    spec = deepcopy(template["spec"])
    for path, value in (overrides or {}).items():
        _set_path(spec, path.split("."), value)
    return spec


def _template_summary(template: dict) -> dict:
    return {
        "id": template["id"],
        "name": template["name"],
        "description": template["description"],
        "risk_label": template["governance"]["risk_label"],
        "parameter_bounds": template["governance"]["parameter_bounds"],
    }


def _momentum_top_n_template() -> dict:
    spec = example_strategy_spec_v1()
    spec["name"] = "momentum_top_n_v1"
    spec["factors"] = [
        {"id": "momentum_20d", "expression": "rank(close / ts_mean(close, 20))", "direction": "higher_is_better", "weight": 1.0}
    ]
    spec["signal_rules"] = {"type": "rank_threshold", "top_n": 30}
    spec["portfolio_rule"] = {"weighting": "score_weighted", "rebalance_period": 5}
    return {
        "id": "momentum_top_n_v1",
        "name": "Momentum Top-N",
        "description": "Score-weighted top-N daily equity momentum candidate.",
        "spec": spec,
        "governance": _governance("medium", max_top_n=80),
    }


def _low_volume_reversal_template() -> dict:
    spec = example_strategy_spec_v1()
    spec["name"] = "low_volume_reversal_v1"
    spec["factors"] = [
        {"id": "short_reversal", "expression": "ts_delta(close, 5)", "direction": "lower_is_better", "weight": 0.7},
        {"id": "volume_anomaly", "expression": "volume / ts_mean(volume, 20)", "direction": "lower_is_better", "weight": 0.3},
    ]
    spec["signal_rules"] = {"type": "rank_threshold", "long_quantile": 0.2}
    spec["portfolio_rule"] = {"weighting": "equal_weight", "rebalance_period": 10}
    return {
        "id": "low_volume_reversal_v1",
        "name": "Low-Volume Reversal",
        "description": "Two-factor reversal candidate with lower volume anomaly preference.",
        "spec": spec,
        "governance": _governance("medium", max_top_n=120),
    }


def _governance(risk_label: str, max_top_n: int) -> dict:
    return {
        "risk_label": risk_label,
        "non_live_trading_notice": "Template is for research review only and is not an order instruction.",
        "parameter_bounds": {
            "signal_rules.top_n": {"min": 1, "max": max_top_n},
            "signal_rules.long_quantile": {"min": 0.01, "max": 1.0},
            "portfolio_rule.rebalance_period": {"min": 1, "max": 60},
            "risk_rules.max_asset_weight": {"min": 0.001, "max": 1.0},
        },
    }


def _set_path(value: dict, path: list[str], new_value) -> None:
    current = value
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = new_value
