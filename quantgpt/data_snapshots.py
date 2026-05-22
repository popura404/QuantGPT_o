"""Deterministic data snapshot metadata helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DataSnapshot

SNAPSHOT_ID_PREFIX = "ds"
FRAME_HASH_COLUMNS = ("trade_date", "stock_code", "open", "high", "low", "close", "volume", "amount", "pct_change")


def build_cache_snapshot(
    cache_path: str | Path,
    *,
    vendor: str = "local_cache",
    query_params: dict[str, Any] | None = None,
    field_schema: dict[str, Any] | list[str] | None = None,
    row_count: int | None = None,
    date_min: str | None = None,
    date_max: str | None = None,
    include_content_hash: bool = True,
) -> dict[str, Any]:
    """Build a deterministic snapshot payload for a local cache read."""
    path = Path(cache_path)
    content_hash = _file_hash(path) if include_content_hash and path.is_file() else None
    stat_payload = {}
    if path.exists():
        stat = path.stat()
        stat_payload = {
            "file_size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    payload = {
        "vendor": vendor,
        "source_kind": "cache_read_snapshot",
        "cache_path": str(path),
        "query_params": _canonical_json_value(query_params or {}),
        "field_schema": _canonical_json_value(field_schema or {}),
        "row_count": row_count,
        "date_min": date_min,
        "date_max": date_max,
        "content_hash": content_hash,
        "stat": stat_payload,
    }
    return _snapshot_payload(payload, download_time=None)


def build_fetch_snapshot(
    *,
    vendor: str,
    endpoint: str,
    query_params: dict[str, Any] | None = None,
    field_schema: dict[str, Any] | list[str] | None = None,
    row_count: int | None = None,
    date_min: str | None = None,
    date_max: str | None = None,
    content_hash: str | None = None,
    download_time: datetime | None = None,
) -> dict[str, Any]:
    """Build a deterministic snapshot payload for a remote fetch result."""
    query = dict(query_params or {})
    query["endpoint"] = endpoint
    payload = {
        "vendor": vendor,
        "source_kind": "remote_fetch_snapshot",
        "cache_path": None,
        "query_params": _canonical_json_value(query),
        "field_schema": _canonical_json_value(field_schema or {}),
        "row_count": row_count,
        "date_min": date_min,
        "date_max": date_max,
        "content_hash": content_hash,
    }
    return _snapshot_payload(payload, download_time=download_time or datetime.now(timezone.utc))


def build_market_frame_snapshot(
    frame: pd.DataFrame,
    *,
    vendor: str = "in_memory_frame",
    source_kind: str = "market_dataframe_snapshot",
    endpoint: str = "market_frame",
    query_params: dict[str, Any] | None = None,
    source_metadata: dict[str, Any] | None = None,
    content_hash: str | None = None,
) -> dict[str, Any]:
    """Build deterministic snapshot metadata from a market-data DataFrame."""
    query = dict(query_params or {})
    query["endpoint"] = endpoint
    payload = {
        "vendor": vendor,
        "source_kind": source_kind,
        "cache_path": None,
        "query_params": _canonical_json_value(query),
        "field_schema": _frame_field_schema(frame),
        "row_count": int(len(frame)),
        "date_min": _frame_date_bound(frame, "min"),
        "date_max": _frame_date_bound(frame, "max"),
        "content_hash": content_hash or _frame_content_hash(frame),
        "source_metadata": _canonical_json_value(source_metadata or {}),
    }
    return _snapshot_payload(payload, download_time=None)


def attach_data_snapshot(
    frame: pd.DataFrame,
    snapshot: dict[str, Any],
    *,
    source_metadata: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Attach snapshot metadata to a DataFrame without changing its data."""
    frame.attrs["data_snapshot"] = snapshot
    frame.attrs["data_snapshot_id"] = snapshot["snapshot_id"]
    frame.attrs["data_source"] = snapshot.get("vendor")
    if source_metadata is not None:
        frame.attrs["source_metadata"] = source_metadata
    return frame


def ensure_market_frame_snapshot(
    frame: pd.DataFrame,
    *,
    vendor: str = "in_memory_frame",
    source_kind: str = "market_dataframe_snapshot",
    endpoint: str = "market_frame",
    query_params: dict[str, Any] | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an existing frame snapshot or attach a deterministic fallback."""
    existing = frame.attrs.get("data_snapshot")
    if isinstance(existing, dict) and existing.get("snapshot_id"):
        return existing
    metadata = source_metadata or frame.attrs.get("source_metadata") or {}
    snapshot = build_market_frame_snapshot(
        frame,
        vendor=vendor,
        source_kind=source_kind,
        endpoint=endpoint,
        query_params=query_params,
        source_metadata=metadata if isinstance(metadata, dict) else {},
    )
    attach_data_snapshot(frame, snapshot, source_metadata=metadata if isinstance(metadata, dict) else {})
    return snapshot


def snapshot_result_fields(
    snapshot: dict[str, Any],
    *,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build public result fields for data provenance."""
    metadata = dict(snapshot)
    if source_metadata:
        metadata["source_metadata"] = _canonical_json_value(source_metadata)
    return {
        "data_snapshot_id": snapshot["snapshot_id"],
        "data_source": snapshot.get("vendor"),
        "data_source_metadata": metadata,
    }


async def persist_data_snapshot(session: AsyncSession, snapshot: dict[str, Any]) -> DataSnapshot:
    """Insert a data snapshot row unless the same snapshot_id already exists."""
    snapshot_id = snapshot["snapshot_id"]
    result = await session.execute(select(DataSnapshot).where(DataSnapshot.snapshot_id == snapshot_id))
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = DataSnapshot(
        snapshot_id=snapshot_id,
        vendor=snapshot.get("vendor"),
        source_kind=snapshot.get("source_kind"),
        cache_path=snapshot.get("cache_path"),
        query_params=snapshot.get("query_params"),
        field_schema=snapshot.get("field_schema"),
        row_count=snapshot.get("row_count"),
        date_min=snapshot.get("date_min"),
        date_max=snapshot.get("date_max"),
        content_hash=snapshot.get("content_hash"),
        download_time=snapshot.get("download_time"),
    )
    session.add(row)
    await session.flush()
    return row


def _snapshot_payload(payload: dict[str, Any], *, download_time: datetime | None) -> dict[str, Any]:
    stable_payload = dict(payload)
    stable_payload.pop("download_time", None)
    snapshot_id = _prefixed_hash(SNAPSHOT_ID_PREFIX, stable_payload)
    output = {
        "snapshot_id": snapshot_id,
        "vendor": payload.get("vendor"),
        "source_kind": payload.get("source_kind"),
        "cache_path": payload.get("cache_path"),
        "query_params": payload.get("query_params"),
        "field_schema": payload.get("field_schema"),
        "row_count": payload.get("row_count"),
        "date_min": payload.get("date_min"),
        "date_max": payload.get("date_max"),
        "content_hash": payload.get("content_hash"),
        "download_time": download_time,
    }
    if "source_metadata" in payload:
        output["source_metadata"] = payload.get("source_metadata")
    return output


def _file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def _prefixed_hash(prefix: str, payload: Any) -> str:
    raw = json.dumps(_canonical_json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def _frame_field_schema(frame: pd.DataFrame) -> dict[str, str]:
    return {str(column): str(dtype) for column, dtype in frame.dtypes.items()}


def _frame_date_bound(frame: pd.DataFrame, bound: str) -> str | None:
    if frame.empty or "trade_date" not in frame.columns:
        return None
    values = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
    if values.empty:
        return None
    selected = values.min() if bound == "min" else values.max()
    return pd.Timestamp(selected).strftime("%Y-%m-%d")


def _frame_content_hash(frame: pd.DataFrame) -> str | None:
    if frame.empty:
        return "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    columns = [column for column in FRAME_HASH_COLUMNS if column in frame.columns]
    if not columns:
        columns = [str(column) for column in frame.columns]
    sample = frame.loc[:, columns].copy()
    sort_columns = [column for column in ("trade_date", "stock_code") if column in sample.columns]
    if sort_columns:
        sample = sample.sort_values(sort_columns).reset_index(drop=True)
    raw = sample.to_json(orient="split", date_format="iso", default_handler=str)
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonical_json_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(v) for v in value]
    if isinstance(value, set):
        return [_canonical_json_value(v) for v in sorted(value, key=str)]
    if isinstance(value, datetime):
        return value.isoformat()
    return value
