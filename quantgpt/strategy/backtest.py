"""Strategy-level backtest pipeline for StrategySpec strategies."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..backtest import _calc_ic_series, _calc_max_drawdown, build_rebalance_dates, compute_factor_values
from ..fundamental_data import detect_fundamental_vars, enrich_market_data
from ..neutralize import neutralize_factor
from .adapters import get_adapter
from .diagnosis import diagnose_strategy_result
from .errors import StrategyValidationError
from .portfolio import build_strategy_portfolio
from .result import StrategyBacktestResult
from .risk import apply_risk_rules
from .signals import build_rank_threshold_signals
from .spec import StrategySpec, StrategySpecV0, StrategySpecV1
from .validation import run_strategy_anti_overfit, run_strategy_rolling_validation
from .validator import validate_strategy_spec


class StrategyBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: StrategySpec
    start_date: str
    end_date: str
    benchmark: str = "hs300"
    universe_date: str | None = None
    rebalance_anchor: str | None = None
    neutralize_industry: bool = False
    neutralize_cap: bool = False

    @field_validator("start_date", "end_date", "universe_date", "rebalance_anchor")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        datetime.strptime(value, "%Y-%m-%d")
        return value

    @model_validator(mode="after")
    def validate_date_order(self):
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be earlier than end_date")
        return self


def run_strategy_backtest(
    request: StrategyBacktestRequest | dict,
    market_df: pd.DataFrame | None = None,
    stock_codes: list[str] | None = None,
) -> StrategyBacktestResult:
    req = request if isinstance(request, StrategyBacktestRequest) else StrategyBacktestRequest.model_validate(request)
    validation = validate_strategy_spec(req.spec)
    if not validation.is_valid:
        raise StrategyValidationError(validation.issues)

    adapter = get_adapter(req.spec.market)
    if market_df is None:
        market_df, stock_codes = adapter.fetch_market_data(
            req.spec.universe,
            req.start_date,
            req.end_date,
            universe_date=req.universe_date,
        )
    if market_df is None or len(market_df) == 0:
        raise ValueError("No market data available for strategy backtest")
    stock_codes = stock_codes or sorted(market_df["stock_code"].dropna().astype(str).unique().tolist())

    expressions = [factor.expression for factor in req.spec.factors]
    fund_vars = set()
    for expression in expressions:
        fund_vars.update(detect_fundamental_vars(expression))
    if fund_vars:
        market_df = enrich_market_data(market_df, fund_vars, stock_codes, req.start_date, req.end_date)

    market_df = market_df.copy()
    market_df["trade_date"] = pd.to_datetime(market_df["trade_date"])
    market_df = market_df.sort_values(["stock_code", "trade_date"])
    if "daily_ret" not in market_df.columns:
        market_df["daily_ret"] = market_df.groupby("stock_code")["close"].pct_change()

    market_df, raw_factor_for_ic = _compute_strategy_factor_values(
        market_df,
        req.spec,
        neutralize_industry=req.neutralize_industry,
        neutralize_cap=req.neutralize_cap,
    )

    all_rebalance_dates = build_rebalance_dates(
        market_df["trade_date"].unique(),
        req.spec.portfolio_rule.rebalance_period,
        req.rebalance_anchor,
    )
    if len(all_rebalance_dates) < 2:
        raise ValueError("Not enough rebalance dates for strategy backtest")

    factor_frame = market_df[["trade_date", "stock_code", "factor_value", "daily_ret", "close"]].dropna(
        subset=["factor_value"]
    ).copy()
    rebalance_frame = factor_frame[factor_frame["trade_date"].isin(all_rebalance_dates)].copy()
    signals = build_rank_threshold_signals(rebalance_frame, req.spec)
    raw_targets = build_strategy_portfolio(signals, req.spec)
    risk_result = apply_risk_rules(raw_targets, req.spec)

    strategy_returns, cost_by_rebalance = _calculate_strategy_returns(
        factor_frame,
        risk_result.target_weights,
        risk_result.turnover_by_rebalance,
        req.spec.cost_model.bps,
    )
    latest_holdings = _latest_holdings(risk_result.target_weights, signals)

    ic_frame = factor_frame.copy()
    ic_frame["factor_value"] = raw_factor_for_ic.reindex(ic_frame.index)
    _, rank_ic_series = _calc_ic_series(ic_frame, req.spec.portfolio_rule.rebalance_period)
    metrics = _strategy_metrics(strategy_returns, risk_result.turnover_by_rebalance, rank_ic_series)

    diagnostics = {
        "factor_flipped_observed": False,
        "strategy_anti_overfit": "not_run",
        "strategy_rolling_validation": "not_run",
    }
    result = StrategyBacktestResult(
        spec=req.spec,
        start_date=req.start_date,
        end_date=req.end_date,
        benchmark=req.benchmark,
        strategy_returns=strategy_returns,
        target_weights=risk_result.target_weights,
        cash_weights=risk_result.cash_weights,
        turnover_by_rebalance=risk_result.turnover_by_rebalance,
        cost_by_rebalance=cost_by_rebalance,
        risk_logs=risk_result.risk_logs,
        latest_holdings=latest_holdings,
        metrics=metrics,
        validation_issues=[],
        diagnostics=diagnostics,
        factor_frame=factor_frame[["trade_date", "stock_code", "factor_value", "daily_ret"]].copy(),
    )
    if req.spec.validation.run_strategy_anti_overfit:
        diagnostics["strategy_anti_overfit"] = run_strategy_anti_overfit(result)
    if req.spec.validation.run_strategy_rolling_validation:
        diagnostics["strategy_rolling_validation"] = run_strategy_rolling_validation(result)
    diagnostics["strategy_diagnosis"] = diagnose_strategy_result(result)
    return result


def _compute_strategy_factor_values(
    market_df: pd.DataFrame,
    spec: StrategySpecV0 | StrategySpecV1,
    *,
    neutralize_industry: bool,
    neutralize_cap: bool,
) -> tuple[pd.DataFrame, pd.Series]:
    market_df = market_df.copy()
    if spec.schema_version == "strategy_spec/v0":
        factor = spec.factors[0]
        values = compute_factor_values(market_df, factor.expression)
        raw_factor_for_ic = values.copy()
        if neutralize_industry or neutralize_cap:
            values = neutralize_factor(
                values,
                market_df,
                industry=neutralize_industry,
                market_cap=neutralize_cap,
            )
        market_df["factor_value"] = values
        return market_df, raw_factor_for_ic

    factor_cols = []
    weighted_cols = []
    total_weight = sum(float(factor.weight) for factor in spec.factors)
    if total_weight <= 0:
        raise ValueError("StrategySpec v1 factor weights must sum to a positive value")

    for idx, factor in enumerate(spec.factors):
        raw_col = f"factor_{idx}_raw"
        score_col = f"factor_{idx}_score"
        market_df[raw_col] = compute_factor_values(market_df, factor.expression)
        values = market_df[raw_col]
        if neutralize_industry or neutralize_cap:
            values = neutralize_factor(
                values,
                market_df,
                industry=neutralize_industry,
                market_cap=neutralize_cap,
            )
            market_df[raw_col] = values
        ascending = factor.direction == "higher_is_better"
        market_df[score_col] = market_df.groupby("trade_date")[raw_col].rank(
            method="average",
            pct=True,
            ascending=ascending,
        )
        factor_cols.append(raw_col)
        weighted_cols.append((score_col, float(factor.weight) / total_weight))

    composite = pd.Series(0.0, index=market_df.index, dtype=float)
    for score_col, weight in weighted_cols:
        composite = composite.add(market_df[score_col].fillna(0.0) * weight, fill_value=0.0)
    market_df["factor_value"] = composite
    return market_df, market_df[factor_cols[0]].copy()


def _calculate_strategy_returns(
    factor_frame: pd.DataFrame,
    target_weights: pd.DataFrame,
    turnover_by_rebalance: pd.DataFrame,
    cost_bps: float,
) -> tuple[pd.Series, pd.DataFrame]:
    if target_weights.empty:
        return pd.Series(dtype=float, name="strategy"), pd.DataFrame(columns=["trade_date", "cost"])

    daily = factor_frame.dropna(subset=["daily_ret"]).copy()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    weights = target_weights.copy()
    weights["trade_date"] = pd.to_datetime(weights["trade_date"])
    weights_by_date = {
        date: group.set_index("stock_code")["target_weight"].astype(float).to_dict()
        for date, group in weights.groupby("trade_date")
    }
    rebalance_dates = sorted(weights_by_date)
    rebal_arr = np.array(rebalance_dates, dtype="datetime64[ns]")
    rows = []
    for trade_date, day in daily.groupby("trade_date", sort=True):
        idx = np.searchsorted(rebal_arr, np.datetime64(trade_date), side="left") - 1
        if idx < 0:
            continue
        weights_for_day = weights_by_date.get(pd.Timestamp(rebal_arr[idx]), {})
        returns_by_stock = day.set_index("stock_code")["daily_ret"].astype(float)
        value = sum(weight * float(returns_by_stock.get(stock_code, 0.0)) for stock_code, weight in weights_for_day.items())
        rows.append((pd.Timestamp(trade_date), value))

    strategy_returns = pd.Series(
        data=[value for _, value in rows],
        index=pd.to_datetime([date for date, _ in rows]),
        name="strategy",
        dtype=float,
    )

    cost_rows = []
    if cost_bps > 0 and not turnover_by_rebalance.empty:
        for row in turnover_by_rebalance.itertuples(index=False):
            rebal_date = pd.Timestamp(row.trade_date)
            future_dates = strategy_returns.index[strategy_returns.index > rebal_date]
            if len(future_dates) == 0:
                continue
            cost = float(row.turnover) * cost_bps / 10000
            if cost <= 0:
                continue
            first_day = future_dates[0]
            strategy_returns.loc[first_day] -= cost
            cost_rows.append({"trade_date": rebal_date, "cost": cost})

    return strategy_returns, pd.DataFrame(cost_rows, columns=["trade_date", "cost"])


def _latest_holdings(target_weights: pd.DataFrame, signals: pd.DataFrame) -> list[dict]:
    if target_weights.empty:
        return []
    latest_date = pd.to_datetime(target_weights["trade_date"]).max()
    latest = target_weights[pd.to_datetime(target_weights["trade_date"]) == latest_date].copy()
    latest_signals = signals[pd.to_datetime(signals["trade_date"]) == latest_date]
    latest = latest.merge(
        latest_signals[["stock_code", "factor_value", "score"]],
        on="stock_code",
        how="left",
    )
    latest = latest.sort_values("target_weight", ascending=False)
    return [
        {
            "trade_date": pd.Timestamp(row.trade_date).strftime("%Y-%m-%d"),
            "stock_code": row.stock_code,
            "target_weight": round(float(row.target_weight), 8),
            "factor_value": None if pd.isna(row.factor_value) else round(float(row.factor_value), 8),
            "score": None if pd.isna(row.score) else round(float(row.score), 8),
        }
        for row in latest.itertuples(index=False)
    ]


def _strategy_metrics(strategy_returns: pd.Series, turnover_by_rebalance: pd.DataFrame, rank_ic_series: pd.Series) -> dict:
    if strategy_returns.empty:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "turnover": 0.0,
            "ic_mean": 0.0,
            "ic_ir": 0.0,
        }
    mean = float(strategy_returns.mean())
    std = float(strategy_returns.std())
    turnover = float(turnover_by_rebalance["turnover"].mean()) if not turnover_by_rebalance.empty else 0.0
    ic_mean = float(rank_ic_series.mean()) if len(rank_ic_series) else 0.0
    ic_std = float(rank_ic_series.std()) if len(rank_ic_series) else 0.0
    return {
        "total_return": float((1 + strategy_returns).prod() - 1),
        "annual_return": float((1 + mean) ** 252 - 1),
        "sharpe": float(mean / std * np.sqrt(252)) if std > 0 else 0.0,
        "max_drawdown": float(_calc_max_drawdown(strategy_returns)),
        "turnover": turnover,
        "ic_mean": ic_mean,
        "ic_ir": float(ic_mean / ic_std) if ic_std > 0 else 0.0,
    }
