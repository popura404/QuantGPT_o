"""Rust engine bridge — QuantGPT
Copyright (c) 2026 Miasyster. Licensed under the MIT License.
https://github.com/Miasyster/QuantGPT

When quantgpt_engine is installed, the Rust engine handles:
  - Expression evaluation (factor computation)
  - Performance metrics (Sharpe, Sortino, etc.)

Trusted research uses Python until the compiled extension passes the v2
differential suite. Installing an extension alone never changes semantics.
"""

import ast
import logging
import os
import platform
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

import numpy as np
import pandas as pd

from .expression_parser import OPERATOR_SEMANTICS_VERSION, _evaluate_panel, parse_expression

logger = logging.getLogger(__name__)


class _RustEngine(Protocol):
    def eval_expression(self, expression: str, columns: dict[str, np.ndarray],
                        stock_offsets: list[tuple[int, int]], date_offsets: list[tuple[int, int]]) -> np.ndarray: ...

    def compute_metrics(self, returns: np.ndarray, periods_per_year: float) -> dict[str, float]: ...


_engine: _RustEngine | None
try:
    extension = import_module("quantgpt_engine")
    if not all(callable(getattr(extension, name, None)) for name in ("eval_expression", "compute_metrics")):
        raise ImportError("quantgpt_engine does not expose the required bridge API")
    _engine = cast(_RustEngine, extension)
    RUST_AVAILABLE = True
    logger.info("Rust engine (quantgpt_engine) loaded")
except ImportError:
    _engine = None
    RUST_AVAILABLE = False

RUST_ENABLED = RUST_AVAILABLE and os.environ.get("QUANTGPT_RUST_ENGINE", "1").lower() in ("1", "true", "yes")
# Deliberately empty until an actual compiled build passes numeric/mask parity.
# A mocked Rust call or an importable extension does not certify an operator.
RUST_VERIFIED_SEMANTICS_VERSION: str | None = None
RUST_METRICS_VERIFIED = False
# This is the scope exercised by the compiled-extension differential suite,
# not permission to enable a build. Keep the build-level gate above separate.
RUST_DIFFERENTIAL_OPERATORS = frozenset({"rank", "scale", "ts_rank", "ts_shift", "ts_delta"})
RUST_DIFFERENTIAL_FIELDS = frozenset({"open", "high", "low", "close", "volume", "amount", "pct_change"})


def rust_expression_supported(expression: str) -> bool:
    """Fail closed outside the finite expression subset tested against Python.

    Mixed unparenthesized arithmetic is excluded because the legacy Python
    parser and Rust parser have different associativity/precedence contracts.
    Derived fields, aliases, comparisons and other operators stay on Python.
    """
    if len(expression) > 1000:
        return False
    try:
        root = ast.parse(expression.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        return False
    source = expression.strip()

    def visit(node: ast.AST, depth: int = 0) -> bool:
        if depth > 100:
            return False
        if isinstance(node, ast.Expression):
            return visit(node.body, depth + 1)
        if isinstance(node, ast.Name):
            return node.id in RUST_DIFFERENTIAL_FIELDS
        if isinstance(node, ast.Constant):
            segment = ast.get_source_segment(source, node) or ""
            if type(node.value) not in (int, float) or "e" in segment.lower():
                return False
            try:
                return bool(np.isfinite(float(cast(int | float, node.value))))
            except (OverflowError, ValueError):
                return False
        if isinstance(node, ast.UnaryOp):
            return isinstance(node.op, ast.USub) and visit(node.operand, depth + 1)
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                return False
            nesting, operators = 0, 0
            for index, char in enumerate(ast.get_source_segment(source, node) or ""):
                if char == "(":
                    nesting += 1
                elif char == ")":
                    nesting -= 1
                elif nesting == 0 and char in "+-*/" and index > 0:
                    operators += 1
            return operators == 1 and visit(node.left, depth + 1) and visit(node.right, depth + 1)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name not in RUST_DIFFERENTIAL_OPERATORS:
                return False
            if name in {"rank", "scale"}:
                return len(node.args) == 1 and visit(node.args[0], depth + 1)
            if len(node.args) != 2:
                return False
            window = node.args[1]
            return (isinstance(window, ast.Constant) and type(window.value) is int
                    and 1 <= window.value <= 500 and visit(node.args[0], depth + 1))
        return False

    return bool(visit(root))


def _rust_input_supported(df: pd.DataFrame, expression: str) -> bool:
    """Native f64 conversion is only certified for already-floating inputs."""
    names = {node.id for node in ast.walk(ast.parse(expression.strip(), mode="eval")) if isinstance(node, ast.Name)}
    fields = names & RUST_DIFFERENTIAL_FIELDS
    return all(field in df and str(df[field].dtype) in {"float64", "Float64"} for field in fields)


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
        "rust_differential_operators": sorted(RUST_DIFFERENTIAL_OPERATORS),
        "rust_differential_fields": sorted(RUST_DIFFERENTIAL_FIELDS),
        "rust_differential_input_dtypes": ["float64", "Float64"],
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
    elif trusted and not rust_expression_supported(expression):
        reason = "rust_expression_outside_verified_subset"
    elif trusted and not _rust_input_supported(df, expression):
        reason = "rust_input_dtype_unverified"
    if reason is None:
        try:
            values = _evaluate_panel(lambda work: _eval_rust_expression(work, expression), df)
            result = FactorEvaluationResult(
                values, "rust", str(getattr(_engine, "__version__", "unknown")), None,
                OPERATOR_SEMANTICS_VERSION,
                trusted and RUST_VERIFIED_SEMANTICS_VERSION == OPERATOR_SEMANTICS_VERSION,
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
    if "stock_code" in df and "trade_date" not in df:
        # Preserve each security's observation order on keyless time axes, but
        # provide contiguous groups to the native engine. _evaluate_panel
        # restores the original positional IDs after this local stable sort.
        df = df.sort_values("stock_code", kind="stable")

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
