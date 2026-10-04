"""Factor group backtest engine (long-only, A-share) — QuantGPT
Copyright (c) 2026 Miasyster. Licensed under the MIT License.
https://github.com/Miasyster/QuantGPT

Splits stocks into quantile groups by factor value on rebalance dates,
holds each group for holding_period days, computes daily equal-weighted
returns per group. The strategy return is the top group's daily return.
"""

import logging
import threading
from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from .expression_parser import parse_expression
from .research.contracts import SEMANTICS_VERSION, SimulationConfigV1
from .research.ledger import simulate_target_weights
from .wq_simulate import wq_simulate

logger = logging.getLogger(__name__)

_api_context = threading.local()


def _require_api_context():
    if not getattr(_api_context, "active", False):
        raise RuntimeError(
            "run_factor_backtest must be called through the API task system. "
            "Direct calls are forbidden. Submit via /api/v1/auto_backtest."
        )


def enable_api_context():
    _api_context.active = True


def disable_api_context():
    _api_context.active = False


from contextlib import contextmanager


@contextmanager
def api_context():
    enable_api_context()
    try:
        yield
    finally:
        disable_api_context()


def compute_factor_values(
    market_df: pd.DataFrame,
    expression: str | None = None,
    precomputed_factor: pd.Series | None = None,
) -> pd.Series:
    """Compute factor values from an expression or align a precomputed series."""
    if precomputed_factor is not None:
        if hasattr(precomputed_factor, "reindex"):
            return precomputed_factor.reindex(market_df.index)
        return precomputed_factor

    if expression is None:
        raise ValueError("必须提供 expression 或 precomputed_factor")

    from .rust_bridge import RUST_ENABLED, eval_factor_expression

    if RUST_ENABLED:
        return eval_factor_expression(market_df, expression)

    factor_func = parse_expression(expression)
    return _safe_apply_factor(market_df, factor_func)


def build_rebalance_dates(
    trade_dates,
    holding_period: int,
    rebalance_anchor: str | None = None,
    calendar_sessions=None,
) -> list:
    """Stride a shared session calendar, then intersect the requested window."""
    if holding_period < 1:
        raise ValueError("holding_period must be positive")
    all_dates = pd.DatetimeIndex(sorted(pd.to_datetime(pd.Series(trade_dates).dropna().unique())))
    if all_dates.empty:
        return []
    calendar = pd.DatetimeIndex(sorted(pd.to_datetime(pd.Series(calendar_sessions).dropna().unique()))) if calendar_sessions is not None else all_dates
    anchor = pd.Timestamp(rebalance_anchor) if rebalance_anchor else calendar[0]
    if anchor not in calendar:
        raise ValueError("CALENDAR_CONTEXT_REQUIRED: rebalance anchor must belong to the complete session sequence")
    if not all_dates.isin(calendar).all():
        raise ValueError("CALENDAR_CONTEXT_REQUIRED: input sessions are absent from the shared calendar")
    anchor_index = int(np.flatnonzero(calendar == anchor)[0])
    selected = calendar[np.arange(len(calendar)) % holding_period == anchor_index % holding_period]
    return list(selected.intersection(all_dates))


def assign_factor_quantiles(
    work: pd.DataFrame,
    rebalance_dates: list,
    n_groups: int,
) -> tuple[pd.DataFrame, pd.DataFrame, list]:
    """Assign factor quantile groups with T+1 rebalance effectiveness."""
    def _assign_group(vals: pd.Series) -> pd.Series:
        # Same-date grouping must not inspect later cross-sections to choose bins.
        distinct = vals.nunique()
        if distinct < 2:
            return pd.Series(np.nan, index=vals.index)
        if distinct < n_groups:
            mapping = {value: index for index, value in enumerate(sorted(vals.dropna().unique()))}
            return vals.map(mapping)
        try:
            ranks = vals.rank(method="first")
            return cast(pd.Series, pd.cut(ranks, bins=n_groups, labels=False))
        except ValueError:
            return pd.Series(np.nan, index=vals.index)

    rebal_data = cast(pd.DataFrame, work.loc[work["trade_date"].isin(rebalance_dates)]).sort_values(["trade_date", "stock_code"]).copy()
    rebal_data["_group"] = rebal_data.groupby("trade_date")["factor_value"].transform(_assign_group)
    rebal_data = rebal_data.dropna(subset=["_group"])
    rebal_data["_group"] = rebal_data["_group"].astype(int)

    group_lookup = cast(pd.Series, rebal_data.set_index(["trade_date", "stock_code"])["_group"])
    rebalance_dates_set = sorted(set(rebal_data["trade_date"].unique()))
    if len(rebalance_dates_set) < 2:
        raise ValueError("Not enough rebalance dates for backtest")

    rebal_arr = np.array(rebalance_dates_set, dtype="datetime64[ns]")
    trade_dates = work["trade_date"].values.astype("datetime64[ns]")
    indices = np.searchsorted(rebal_arr, trade_dates, side="left") - 1
    valid_mask = indices >= 0
    work = cast(pd.DataFrame, work.loc[valid_mask]).copy()
    work["_rebal_date"] = rebal_arr[indices[valid_mask]]
    work = work.dropna(subset=["daily_ret"])

    work = work.merge(
        group_lookup.rename("_group"),
        left_on=["_rebal_date", "stock_code"],
        right_index=True,
        how="left",
    )
    work = work.dropna(subset=["_group"])
    work["_group"] = work["_group"].astype(int)

    if work["_group"].nunique() < 2:
        raise ValueError("Could not form enough quantile groups")

    return work, rebal_data, rebalance_dates_set


def calculate_turnover_from_weights(
    weights_by_date: dict | pd.DataFrame,
    holding_period: int = 1,
) -> float:
    """Calculate average daily turnover from target-weight snapshots."""
    if isinstance(weights_by_date, pd.DataFrame):
        weights_frame = cast(pd.DataFrame, weights_by_date)
        required = {"trade_date", "stock_code", "target_weight"}
        missing = required - set(weights_frame.columns)
        if missing:
            raise ValueError(f"weights_by_date missing columns: {sorted(missing)}")
        weight_map = {
            date: group.set_index("stock_code")["target_weight"].astype(float).to_dict()
            for date, group in weights_frame.groupby("trade_date")
        }
    else:
        weight_map = weights_by_date

    sorted_dates = sorted(cast(list[Any], list(weight_map)))
    if len(sorted_dates) < 2:
        return 0.0

    turnovers = []
    for i in range(1, len(sorted_dates)):
        prev = weight_map.get(sorted_dates[i - 1], {})
        curr = weight_map.get(sorted_dates[i], {})
        assets = set(prev) | set(curr)
        turnover = sum(abs(float(curr.get(asset, 0.0)) - float(prev.get(asset, 0.0))) for asset in assets) / 2
        turnovers.append(turnover)

    per_rebal = float(np.mean(turnovers)) if turnovers else 0.0
    return per_rebal / holding_period


def run_factor_backtest(
    market_df: pd.DataFrame,
    expression: str | None = None,
    n_groups: int = 5,
    holding_period: int = 5,
    cost_rate: float = 0.003,
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
    precomputed_factor: pd.Series | None = None,
    trading_days_per_year: int = 252,
    rebalance_anchor: str | None = None,
    direction_mode: str = "auto_full",
    fixed_direction: int | None = None,
    simulation_config: SimulationConfigV1 | None = None,
    evaluation_start: str | None = None,
    evaluation_end: str | None = None,
) -> dict:
    """Run quantile group backtest on a factor expression (long-only).

    IMPORTANT: Must be called through API task system. Direct calls raise RuntimeError.

    Strategy: on each rebalance date (every holding_period trading days),
    rank all stocks by factor value, split into n_groups quantile groups,
    hold each group until next rebalance. Report daily returns for each group.

    The "strategy" return series is the top group (highest factor values).

    Args:
        market_df: DataFrame with columns trade_date, stock_code, open, high,
                   low, close, volume, amount, pct_change.
        expression: Factor expression string. Can be None if precomputed_factor is provided.
        n_groups: Number of quantile groups.
        holding_period: Days between rebalances.
        cost_rate: Single rebalance cost rate (default 0.3% = commission + stamp tax + slippage).
        precomputed_factor: Pre-computed factor values (Series indexed like market_df).
                           If provided, expression is ignored.

    Returns:
        Dict with keys: strategy_returns (daily Series), group_returns,
        top_group_sharpe, monotonicity_score, spread, cost_adjusted, etc.
    """
    _require_api_context()
    direction_mode = direction_mode or "auto_full"
    if direction_mode not in {"auto_full", "fixed"}:
        raise ValueError("direction_mode must be 'auto_full' or 'fixed'")
    if direction_mode == "auto_full" and fixed_direction is not None:
        raise ValueError("fixed_direction must be null when direction_mode='auto_full'")
    if direction_mode == "fixed" and fixed_direction not in (1, -1):
        raise ValueError("fixed_direction must be exactly 1 or -1 when direction_mode='fixed'")

    # 1. Compute factor values
    market_df = market_df.copy()
    market_df["trade_date"] = pd.to_datetime(market_df["trade_date"])
    market_df = market_df.sort_values(["stock_code", "trade_date"])
    market_df["factor_value"] = compute_factor_values(market_df, expression, precomputed_factor)
    market_df["factor_value"] = market_df["factor_value"].replace([np.inf, -np.inf], np.nan)
    if expression is not None and precomputed_factor is None:
        from .expression_parser import infer_expression_lookback
        lookback = infer_expression_lookback(expression)
        observations = market_df.groupby("stock_code").cumcount() + 1
        market_df.loc[observations < lookback.required_observations, "factor_value"] = np.nan

    # Save raw factor values for IC computation (before neutralization).
    # IC should be computed on raw values (industry standard), while group
    # formation uses neutralized values to control sector/cap risk.
    raw_factor_for_ic = cast(pd.Series, market_df["factor_value"]).copy()

    # 1b. Neutralize factor values (optional)
    if neutralize_industry or neutralize_cap:
        from .neutralize import neutralize_factor
        market_df["factor_value"] = neutralize_factor(
            cast(pd.Series, market_df["factor_value"]),
            market_df,
            industry=neutralize_industry,
            market_cap=neutralize_cap,
        )

    # 3. Compute daily returns from close prices (T-1 close → T close)
    market_df["daily_ret"] = market_df.groupby("stock_code")["close"].pct_change(fill_method=None)

    # 4. Identify rebalance dates
    rebalance_dates = build_rebalance_dates(
        market_df["trade_date"].unique(),
        holding_period,
        rebalance_anchor,
        calendar_sessions=market_df.attrs.get("calendar_sessions"),
    )

    # 5. On each rebalance date, assign groups based on factor value
    #    Build a mapping: (trade_date, stock_code) -> group
    work = cast(pd.DataFrame, market_df[["trade_date", "stock_code", "factor_value", "daily_ret", "close"]]).dropna(
        subset=["factor_value"]
    ).copy()
    work, rebal_data, rebalance_dates_set = assign_factor_quantiles(work, rebalance_dates, n_groups)

    # 6. Each group is a quantity/cash book with open fills and explicit fees.
    simulation = simulation_config or SimulationConfigV1(
        rebalance_anchor_session=cast(date, pd.Timestamp(str(rebalance_anchor or market_df["trade_date"].min())).date()),
        fees_bps=cost_rate * 10000,
        rebalance_every_sessions=holding_period,
        currency="CNY",
    )
    if simulation.evaluation_start_state == "carry_forward":
        raise ValueError("CAPABILITY_BLOCKER: factor groups require separate carry-forward state per group")
    actual_groups = sorted(rebal_data["_group"].unique())
    ledgers = {}
    first_fill = market_df.loc[market_df["trade_date"] > min(rebalance_dates_set), "trade_date"].min()
    for group_id in actual_groups:
        targets = rebal_data.loc[rebal_data["_group"] == group_id, ["trade_date", "stock_code"]].copy()
        targets["target_weight"] = 1.0 / targets.groupby("trade_date")["stock_code"].transform("count")
        ledgers[group_id] = simulate_target_weights(
            market_df, targets, simulation,
            evaluation_start=evaluation_start or first_fill,
            evaluation_end=evaluation_end,
        )
    daily_group_ret = pd.DataFrame({group: ledger.returns for group, ledger in ledgers.items()})
    cost_adjusted = simulation.fees_bps + simulation.slippage_bps > 0
    total_cost_drag = sum(float(ledger.costs["cost"].sum()) for ledger in ledgers.values())

    top_g = actual_groups[-1]
    bot_g = actual_groups[0]

    # Direction policy.
    # auto_full preserves the legacy full-period return-based flip.
    # fixed is used by OOS validation so valid/test never choose their own direction.
    top_mean = daily_group_ret[top_g].mean()
    bot_mean = daily_group_ret[bot_g].mean()
    flipped = False
    direction_source = "auto_full_deprecated"
    direction_warning = (
        "auto_full uses full-period realized returns to choose factor direction; "
        "use OOS train-fixed direction for unbiased evaluation."
    )
    effective_direction = 1
    if direction_mode == "auto_full" and bot_mean > top_mean:
        flipped = True
        top_g, bot_g = bot_g, top_g
        effective_direction = -1
        logger.info("Factor direction flipped: low factor values outperform high values")
    elif direction_mode == "fixed":
        direction_source = "fixed"
        direction_warning = None
        effective_direction = fixed_direction or 1
        if effective_direction == -1:
            flipped = True
            top_g, bot_g = bot_g, top_g

    # 7. Strategy = best-performing group (long-only, A-share)
    strategy_series = cast(pd.Series, daily_group_ret[top_g]).copy()
    strategy_series.name = "strategy"
    strategy_series.index = pd.to_datetime(strategy_series.index)

    # Also compute long-short for metrics (informational only)
    # Informational 50% long / 50% short sleeve return: normalize gross exposure
    # to one and subtract BOTH sleeves' costs, never subtract a net short return.
    top_book, bottom_book = ledgers[top_g], ledgers[bot_g]
    top_cost = top_book.costs.set_index("trade_date")["cost"]
    bottom_cost = bottom_book.costs.set_index("trade_date")["cost"]
    ls_series = (top_book.gross_returns - bottom_book.gross_returns - top_cost - bottom_cost) / 2

    # 8. Metrics
    annualize = np.sqrt(trading_days_per_year)
    strat_mean, strat_std = strategy_series.mean(), strategy_series.std()
    top_sharpe = float((strat_mean / strat_std * annualize) if strat_std > 0 else 0.0)

    ls_mean, ls_std = ls_series.mean(), ls_series.std()
    ls_sharpe = float((ls_mean / ls_std * annualize) if ls_std > 0 else 0.0)
    ls_annual = float((1 + ls_mean) ** trading_days_per_year - 1)

    group_means = [float(cast(pd.Series, daily_group_ret[g]).mean()) for g in actual_groups]
    mono = _calc_monotonicity(group_means)

    # If flipped, reverse group_means for spread calculation so spread is always positive
    spread = float(group_means[-1] - group_means[0])
    if flipped:
        spread = -spread

    # 9. IC / Rank IC / IR / IC win rate
    # Use raw (pre-neutralization) factor values for IC — industry standard.
    # Neutralization is for portfolio construction only, not IC measurement.
    # Primary IC metric is Rank IC (Spearman) — more robust to outliers,
    # consistent with industry convention (Barra, etc.).
    work_ic = cast(pd.DataFrame, market_df[["trade_date", "stock_code", "close", "factor_value"]]).copy()
    if evaluation_end is not None:
        work_ic = cast(pd.DataFrame, work_ic.loc[work_ic["trade_date"] <= pd.Timestamp(evaluation_end)])
    work_ic["factor_value"] = raw_factor_for_ic.reindex(work_ic.index)
    pearson_ic_series, rank_ic_series = _calc_ic_series(work_ic, holding_period)
    # Warmup observations are inputs to the factor, not scored observations.
    # Build complete labels first (already capped at end above), then retain
    # only signal dates inside the registered evaluation window.
    if evaluation_start is not None:
        first_scored_session = pd.Timestamp(evaluation_start)
        pearson_ic_series = pearson_ic_series.loc[pearson_ic_series.index >= first_scored_session]
        rank_ic_series = rank_ic_series.loc[rank_ic_series.index >= first_scored_session]
    direction_adjusted_ic_series = pearson_ic_series * effective_direction
    direction_adjusted_rank_ic_series = rank_ic_series * effective_direction
    # Main IC metrics use Rank IC (Spearman)
    ic_mean = float(rank_ic_series.mean()) if len(rank_ic_series) > 0 else 0.0
    ic_std = float(rank_ic_series.std()) if len(rank_ic_series) > 0 else 0.0
    ic_ir = float(ic_mean / ic_std) if ic_std > 0 else 0.0
    ic_win_rate = float((rank_ic_series > 0).sum() / len(rank_ic_series)) if len(rank_ic_series) > 0 else 0.0
    rank_ic_mean = ic_mean  # same as ic_mean now (both Spearman)
    raw_ic_mean = float(pearson_ic_series.mean()) if len(pearson_ic_series) > 0 else 0.0
    raw_rank_ic_mean = rank_ic_mean
    direction_adjusted_ic_mean = (
        float(direction_adjusted_ic_series.mean()) if len(direction_adjusted_ic_series) > 0 else 0.0
    )
    direction_adjusted_rank_ic_mean = (
        float(direction_adjusted_rank_ic_series.mean()) if len(direction_adjusted_rank_ic_series) > 0 else 0.0
    )

    # 10. Turnover rate (daily, WQ BRAIN-aligned)
    turnover = float(ledgers[top_g].turnover["turnover"].mean())
    selected_group_holdings = _calc_group_holdings(work, top_g, rebalance_dates_set)
    turnover_by_rebalance = ledgers[top_g].turnover.set_index("trade_date")["turnover"]

    group_ret_summary = {}
    for g in actual_groups:
        s = cast(pd.Series, daily_group_ret[g])
        std = s.std()
        group_ret_summary[int(g)] = {
            "group": f"G{int(g)+1}",
            "mean_return": float(s.mean()),
            "annual_return": float((1 + s.mean()) ** trading_days_per_year - 1),
            "sharpe": float((s.mean() / std * annualize) if std > 0 else 0.0),
            "max_drawdown": float(_calc_max_drawdown(s)),
        }

    # 11. Stock factor data — extract latest rebalance factor values + period returns
    stock_factor_data = None
    if len(rebalance_dates_set) > 0:
        last_rebal = rebalance_dates_set[-1]
        last_rebal_data = cast(pd.DataFrame, rebal_data.loc[rebal_data["trade_date"] == last_rebal]).copy()
        if not last_rebal_data.empty:
            # Percentile rank: high rank = stronger signal (direction-aware)
            last_rebal_data["factor_rank"] = last_rebal_data["factor_value"].rank(
                ascending=(not flipped), pct=True
            )
            # Per-stock cumulative return over the backtest period (vectorized)
            period_ret_by_stock = (
                work.groupby("stock_code")["daily_ret"]
                .agg(lambda s: float((1 + s).prod() - 1))
            )
            stocks_list = []
            for row in last_rebal_data.sort_values("factor_rank", ascending=False).to_dict(orient="records"):
                g_idx = int(row["_group"])
                sc = row["stock_code"]
                stocks_list.append({
                    "stock_code": sc,
                    "factor_value": round(float(row["factor_value"]), 6),
                    "direction_adjusted_factor_value": round(float(row["factor_value"] * effective_direction), 6),
                    "factor_rank": round(float(row["factor_rank"]), 4),
                    "group": g_idx,
                    "group_label": f"G{g_idx + 1}",
                    "period_return": round(float(cast(Any, period_ret_by_stock.get(sc, 0.0))), 6),
                })
            stock_factor_data = {
                "rebalance_date": str(last_rebal.date()) if hasattr(last_rebal, 'date') else str(last_rebal)[:10],
                "flipped": flipped,
                "fixed_direction": effective_direction,
                "direction_source": direction_source,
                "total_stock_count": len(last_rebal_data),
                "stocks": stocks_list,
            }

    # 12. WorldQuant Fitness (approx, based on group backtest metrics)
    wq_fitness = 0.0
    if ls_sharpe != 0 and turnover > 0:
        effective_turnover = max(turnover, 0.125)
        wq_fitness = float(ls_sharpe * np.sqrt(abs(ls_annual) / effective_turnover))

    # 13. WQ BRAIN dollar-neutral simulation (continuous weights, WQ-aligned metrics)
    wq_work = cast(pd.DataFrame, work[["trade_date", "stock_code", "factor_value", "daily_ret"]]).copy()
    if effective_direction == -1:
        wq_work["factor_value"] = -wq_work["factor_value"]
    wq_brain = wq_simulate(wq_work, rebalance_dates_set, trading_days_per_year)

    factor_df = work[["trade_date", "stock_code", "factor_value", "daily_ret"]].copy()
    direction_adjusted_factor_df = factor_df.copy()
    direction_adjusted_factor_df["factor_value"] = direction_adjusted_factor_df["factor_value"] * effective_direction

    return {
        "strategy_returns": strategy_series,
        "ls_returns": ls_series,  # kept for backward compat
        "group_returns": group_ret_summary,
        "long_short_sharpe": ls_sharpe,
        "long_short_annual": ls_annual,
        "top_group_sharpe": top_sharpe,
        "monotonicity_score": float(mono),
        "spread": spread,
        "flipped": flipped,
        "direction_mode": direction_mode,
        "direction_source": direction_source,
        "direction_basis": "cost_adjusted_group_mean",
        "fixed_direction": effective_direction,
        "direction_warning": direction_warning,
        "ic_mean": ic_mean,
        "rank_ic_mean": rank_ic_mean,
        "raw_ic_mean": raw_ic_mean,
        "raw_rank_ic_mean": raw_rank_ic_mean,
        "direction_adjusted_ic_mean": direction_adjusted_ic_mean,
        "direction_adjusted_rank_ic_mean": direction_adjusted_rank_ic_mean,
        "ic_ir": ic_ir,
        "ic_win_rate": ic_win_rate,
        "turnover": turnover,
        "turnover_source": "ledger_traded_notional_over_two_nav_daily",
        "wq_fitness": round(wq_fitness, 4),
        "wq_brain": wq_brain,
        "cost_adjusted": cost_adjusted,
        "cost_rate": cost_rate,
        "semantics_version": SEMANTICS_VERSION,
        "simulation_config": simulation.model_dump(mode="json"),
        "long_short_gross_exposure": 1.0,
        "long_short_scope": "informational_unfinanced_spread; not an executable short strategy",
        "_group_ledgers": ledgers,
        "holding_period": holding_period,
        "total_cost_drag": round(total_cost_drag, 6),
        "_factor_df": factor_df,
        "_direction_adjusted_factor_df": direction_adjusted_factor_df,
        "_raw_ic_series": pearson_ic_series,
        "_raw_rank_ic_series": rank_ic_series,
        "_direction_adjusted_ic_series": direction_adjusted_ic_series,
        "_direction_adjusted_rank_ic_series": direction_adjusted_rank_ic_series,
        "_selected_group_holdings": selected_group_holdings,
        "_turnover_by_rebalance": turnover_by_rebalance,
        "_stock_factor_data": stock_factor_data,
    }


def _safe_apply_factor(df: pd.DataFrame, factor_func) -> pd.Series:
    """Apply with row alignment; capability/parse errors must fail the run."""
    result = factor_func(df)
    if isinstance(result, pd.Series):
        return result.reindex(df.index)
    return pd.Series(result, index=df.index, dtype=float)


def _calc_max_drawdown(returns: pd.Series) -> float:
    """Calculate max drawdown from a return series."""
    cumulative = (1 + returns).cumprod()
    peak = cumulative.cummax().clip(lower=1.0)
    drawdown = (cumulative - peak) / peak
    return float(drawdown.min()) if len(drawdown) > 0 else 0.0


def _calc_ic_series(
    work: pd.DataFrame, holding_period: int
) -> tuple:
    """Calculate per-period IC and Rank IC series.

    IC = Pearson correlation between factor value and forward N-day return
    Rank IC = Spearman rank correlation (more robust to outliers)

    Returns (ic_series, rank_ic_series) as pd.Series indexed by date.
    """
    work = work.copy()
    work = work.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
    work["trade_date"] = pd.to_datetime(work["trade_date"])

    # Calendar-based forward returns: every stock on date T uses the same
    # market trading date T+N, so suspensions or missing rows do not shift
    # individual stocks onto a different horizon.
    all_dates = sorted(work["trade_date"].dropna().unique())
    date_fwd_map = {
        all_dates[i]: all_dates[i + holding_period]
        for i in range(len(all_dates) - holding_period)
    }
    work["_fwd_date"] = cast(pd.Series, work["trade_date"]).map(date_fwd_map)
    future_close = cast(pd.DataFrame, work[["trade_date", "stock_code", "close"]]).rename(
        columns={"trade_date": "_fwd_date", "close": "_fwd_close"}
    )
    work = work.merge(future_close, on=["_fwd_date", "stock_code"], how="left")
    work["fwd_ret"] = np.where(
        (work["close"] > 0) & work["_fwd_close"].notna(),
        work["_fwd_close"] / work["close"] - 1,
        np.nan,
    )
    work = work.drop(columns=["_fwd_date", "_fwd_close"])

    valid = work.dropna(subset=["factor_value", "fwd_ret"])
    if valid.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    def _pearson(g):
        if len(g) < 10:
            return np.nan
        fv = g["factor_value"]
        fr = g["fwd_ret"]
        if fv.nunique() < 2 or fr.nunique() < 2:
            return np.nan
        return fv.corr(fr)

    def _spearman(g):
        if len(g) < 10:
            return np.nan
        fv = g["factor_value"]
        fr = g["fwd_ret"]
        if fv.nunique() < 2 or fr.nunique() < 2:
            return np.nan
        corr, _ = sp_stats.spearmanr(fv.values, fr.values)
        coefficient = cast(float, corr)
        return coefficient if not np.isnan(coefficient) else 0.0

    ic_series = valid.groupby("trade_date")[["factor_value", "fwd_ret"]].apply(_pearson).dropna()
    rank_ic_series = valid.groupby("trade_date")[["factor_value", "fwd_ret"]].apply(_spearman).dropna()
    return ic_series, rank_ic_series


def _calc_turnover(
    work: pd.DataFrame, top_group: int, rebalance_dates: list,
    holding_period: int = 1,
) -> float:
    """Calculate average daily turnover for the top group (WQ BRAIN-aligned).

    Per-rebalance turnover = (entering + exiting) / avg_portfolio_size.
    Daily turnover = per_rebalance / holding_period.
    """
    if len(rebalance_dates) < 2:
        return 0.0

    top_holdings = {}
    for d in rebalance_dates:
        day_data = cast(pd.DataFrame, work.loc[(work["_rebal_date"] == d) & (work["_group"] == top_group)])
        top_holdings[d] = set(day_data["stock_code"].unique())

    turnovers = []
    sorted_dates = sorted(top_holdings.keys())
    for i in range(1, len(sorted_dates)):
        prev = top_holdings[sorted_dates[i - 1]]
        curr = top_holdings[sorted_dates[i]]
        avg_size = (len(prev) + len(curr)) / 2
        if avg_size == 0:
            continue
        entering = len(curr - prev)
        exiting = len(prev - curr)
        turnovers.append((entering + exiting) / avg_size)

    per_rebal = float(np.mean(turnovers)) if turnovers else 0.0
    return per_rebal / holding_period


def _calc_group_holdings(
    work: pd.DataFrame,
    selected_group: int,
    rebalance_dates: list,
) -> dict:
    """Return selected-group holdings by rebalance date for masked OOS turnover."""
    holdings = {}
    for d in rebalance_dates:
        day_data = cast(pd.DataFrame, work.loc[(work["_rebal_date"] == d) & (work["_group"] == selected_group)])
        holdings[pd.Timestamp(d)] = set(day_data["stock_code"].unique())
    return holdings


def _calc_turnover_by_rebalance(
    holdings: dict,
    holding_period: int = 1,
) -> pd.Series:
    """Calculate selected-group daily turnover for each rebalance transition."""
    if len(holdings) < 2:
        return pd.Series(dtype=float)

    sorted_dates = sorted(holdings.keys())
    turnovers = {}
    for i in range(1, len(sorted_dates)):
        prev = holdings.get(sorted_dates[i - 1], set())
        curr = holdings.get(sorted_dates[i], set())
        avg_size = (len(prev) + len(curr)) / 2
        if avg_size == 0:
            turnovers[sorted_dates[i]] = 0.0
            continue
        entering = len(curr - prev)
        exiting = len(prev - curr)
        turnovers[sorted_dates[i]] = ((entering + exiting) / avg_size) / holding_period
    return pd.Series(turnovers, dtype=float)


def _calc_monotonicity(group_means: list[float]) -> float:
    """Spearman rank correlation between group index and mean return."""
    if len(group_means) < 3:
        return 0.0
    ranks = list(range(len(group_means)))
    corr, _ = sp_stats.spearmanr(ranks, group_means)
    coefficient = float(cast(float, corr))
    return abs(coefficient) if not np.isnan(coefficient) else 0.0


def _calc_per_group_turnover(
    work: pd.DataFrame,
    rebalance_dates: list,
    n_groups: int,
) -> dict[int, pd.Series]:
    """Calculate turnover per group on each rebalance date.

    Returns:
        Dict mapping group_id -> Series indexed by rebalance_date with turnover values.
    """
    # Build holdings per (rebal_date, group)
    holdings: dict[tuple, set] = {}
    for d in rebalance_dates:
        for g in range(n_groups):
            day_data = cast(pd.DataFrame, work.loc[(work["_rebal_date"] == d) & (work["_group"] == g)])
            holdings[(d, g)] = set(day_data["stock_code"].unique())

    sorted_dates = sorted(set(d for d, _ in holdings))
    result = {}
    for g in range(n_groups):
        turnovers = {}
        for i in range(1, len(sorted_dates)):
            prev = holdings.get((sorted_dates[i - 1], g), set())
            curr = holdings.get((sorted_dates[i], g), set())
            if len(prev) == 0 and len(curr) == 0:
                turnovers[sorted_dates[i]] = 0.0
                continue
            union = prev | curr
            changed = len(prev.symmetric_difference(curr))
            turnovers[sorted_dates[i]] = changed / len(union) if len(union) > 0 else 0.0
        result[g] = pd.Series(turnovers, dtype=float)
    return result
