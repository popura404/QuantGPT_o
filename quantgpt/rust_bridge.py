"""Rust engine bridge — QuantGPT
Copyright (c) 2026 Miasyster. Licensed under the MIT License.
https://github.com/Miasyster/QuantGPT

When quantgpt_engine is installed, the Rust engine handles:
  - Expression evaluation (factor computation)
  - Performance metrics (Sharpe, Sortino, etc.)

Trusted research uses Python until the compiled extension passes the v2
differential suite. Installing an extension alone never changes semantics.
"""

import logging
import os
import platform
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .expression_parser import OPERATOR_SEMANTICS_VERSION, _evaluate_panel, parse_expression

logger = logging.getLogger(__name__)

try:
    import quantgpt_engine as _engine
    RUST_AVAILABLE = True
    logger.info("Rust engine (quantgpt_engine) loaded")
except ImportError:
    _engine = None  # type: ignore[assignment]
    RUST_AVAILABLE = False

RUST_ENABLED = RUST_AVAILABLE and os.environ.get("QUANTGPT_RUST_ENGINE", "1").lower() in ("1", "true", "yes")
# Deliberately empty until an actual compiled build passes numeric/mask parity.
# A mocked Rust call or an importable extension does not certify an operator.
RUST_VERIFIED_SEMANTICS_VERSION: str | None = None
RUST_METRICS_VERIFIED = False


@dataclass(frozen=True)
class FactorEvaluationResult:
    values: pd.Series
    engine_used: str
    engine_version: str
    fallback_reason: str | None
    semantics_version: str
    contract_verified: bool

    def metadata(self) -> dict:
        return {
            "engine_used": self.engine_used,
            "engine_version": self.engine_version,
            "fallback_reason": self.fallback_reason,
            "semantics_version": self.semantics_version,
            "contract_verified": self.contract_verified,
        }


def engine_capabilities() -> dict:
    """Report availability separately from verified numerical capability."""
    return {
        "rust_available": RUST_AVAILABLE,
        "rust_enabled": RUST_ENABLED,
        "rust_semantics_verified": RUST_VERIFIED_SEMANTICS_VERSION == OPERATOR_SEMANTICS_VERSION,
        "rust_metrics_verified": RUST_METRICS_VERIFIED,
        "default_engine": "python",
        "semantics_version": OPERATOR_SEMANTICS_VERSION,
    }


def eval_factor_expression(df: pd.DataFrame, expression: str, *, trusted: bool = True) -> pd.Series:
    """Compatibility Series API with actual engine metadata in ``attrs``.

    ``trusted=False`` is an explicit experimental acceleration opt-in; its
    result is marked unverified and must not become research promotion evidence.
    """
    return eval_factor_expression_with_metadata(df, expression, trusted=trusted).values


def eval_factor_expression_with_metadata(
    df: pd.DataFrame, expression: str, *, trusted: bool = True,
) -> FactorEvaluationResult:
    """Evaluate with a serializable explanation of the actual engine choice."""
    reason: str | None = None
    if _engine is None:
        reason = "rust_unavailable"
    elif not RUST_ENABLED:
        reason = "rust_disabled"
    elif trusted and RUST_VERIFIED_SEMANTICS_VERSION != OPERATOR_SEMANTICS_VERSION:
        reason = "rust_semantics_unverified"
    if reason is None:
        try:
            values = _evaluate_panel(lambda work: _eval_rust_expression(work, expression), df)
            result = FactorEvaluationResult(
                values, "rust", str(getattr(_engine, "__version__", "unknown")), None,
                OPERATOR_SEMANTICS_VERSION,
                RUST_VERIFIED_SEMANTICS_VERSION == OPERATOR_SEMANTICS_VERSION,
            )
            values.attrs.update(result.metadata())
            return result
        except Exception as exc:
            reason = f"rust_evaluation_failed:{type(exc).__name__}"
            logger.warning("Rust eval_expression failed; using Python (%s)", type(exc).__name__)
    values = parse_expression(expression)(df)
    result = FactorEvaluationResult(
        values, "python", f"python/{platform.python_version()};pandas/{pd.__version__};numpy/{np.__version__}",
        reason, OPERATOR_SEMANTICS_VERSION, True,
    )
    values.attrs.update(result.metadata())
    return result


def _eval_rust_expression(df: pd.DataFrame, expression: str) -> pd.Series:
    engine = _engine
    if engine is None:
        raise RuntimeError("Rust engine unavailable")

    columns = {}
    for col in df.columns:
        if col in ("trade_date", "stock_code", "session", "security_id"):
            continue
        try:
            columns[col] = df[col].to_numpy(dtype=np.float64, na_value=np.nan)
        except (ValueError, TypeError):
            continue

    # Build stock group offsets (data is sorted by stock_code, trade_date)
    stock_offsets = []
    date_offsets = []

    if "stock_code" in df.columns and len(df):
        sc = df["stock_code"].values
        start = 0
        for i in range(1, len(sc)):
            if sc[i] != sc[start]:
                stock_offsets.append((start, i))
                start = i
        stock_offsets.append((start, len(sc)))

    # Compute derived columns
    if "vwap" not in columns and "amount" in columns and "volume" in columns:
        vol = columns["volume"]
        amt = columns["amount"]
        with np.errstate(divide="ignore", invalid="ignore"):
            columns["vwap"] = np.where(vol > 0, amt / vol, columns.get("close", np.full(len(df), np.nan)))

    if "returns" not in columns and "close" in columns:
        columns["returns"] = _compute_grouped_returns(columns["close"], stock_offsets)

    # Pass trade_date as numeric column so Rust can build proper
    # cross-sectional groups (data is sorted by stock_code, not trade_date).
    if "trade_date" in df.columns:
        td = df["trade_date"]
        if hasattr(td.dtype, "name") and "datetime" in td.dtype.name:
            columns["__date__"] = td.values.astype("int64").astype(np.float64)
        else:
            columns["__date__"] = pd.to_datetime(td).values.astype("int64").astype(np.float64)

    result = engine.eval_expression(expression, columns, stock_offsets, date_offsets)
    return pd.Series(result, index=df.index, name="factor_value")


def compute_metrics_rust(daily_returns: pd.Series, periods_per_year: int = 252, *, trusted: bool = True) -> dict:
    """Return an empty dict to invoke Python until metrics parity is verified."""
    engine = _engine
    if not RUST_ENABLED or engine is None or (trusted and not RUST_METRICS_VERIFIED):
        return {}

    rets = daily_returns.to_numpy(dtype=np.float64, na_value=0.0)
    try:
        return dict(engine.compute_metrics(rets, float(periods_per_year)))
    except Exception:
        return {}


def _compute_grouped_returns(close: np.ndarray, stock_offsets: list[tuple[int, int]]) -> np.ndarray:
    ret = np.full(len(close), np.nan, dtype=np.float64)
    groups = stock_offsets or [(0, len(close))]
    for start, end in groups:
        if end - start < 2:
            continue
        prev = close[start:end - 1]
        curr = close[start + 1:end]
        with np.errstate(divide="ignore", invalid="ignore"):
            values = np.where(prev != 0, (curr - prev) / prev, np.nan)
        ret[start + 1:end] = values
    return ret
