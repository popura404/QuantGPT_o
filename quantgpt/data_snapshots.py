"""Deterministic data snapshot metadata helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from filelock import FileLock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DataSnapshot

SNAPSHOT_ID_PREFIX = "ds"
FRAME_HASH_COLUMNS = ("trade_date", "stock_code", "open", "high", "low", "close", "volume", "amount", "pct_change")
INPUT_ATTRIBUTES = ("corporate_actions", "calendar_sessions", "calendar_version", "adjustment", "data_provenance",
                    "research_only", "capability_blockers", "universe_membership")


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
    metadata = dict(source_metadata or {})
    metadata.pop("input_attributes", None)
    attributes = {name: frame.attrs[name] for name in INPUT_ATTRIBUTES if name in frame.attrs}
    if attributes:
        metadata["input_attributes"] = _canonical_json_value(attributes)
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
        "source_metadata": _canonical_json_value(metadata),
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
    current_attrs = _canonical_json_value({name: frame.attrs[name] for name in INPUT_ATTRIBUTES if name in frame.attrs})
    existing_attrs = existing.get("source_metadata", {}).get("input_attributes", {}) if isinstance(existing, dict) else {}
    if (isinstance(existing, dict) and existing.get("snapshot_id") and current_attrs == existing_attrs
            and existing.get("content_hash") == _frame_content_hash(frame)
            and existing.get("field_schema") == _frame_field_schema(frame)):
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
    output["snapshot_schema_version"] = "data_snapshot/v2"
    output["replayable"] = False
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
    columns = sorted(frame.columns, key=str)
    sample = frame.loc[:, columns].copy()
    sort_columns = [column for column in ("session", "security_id", "trade_date", "stock_code",
                                         "available_at", "period_end", "revision_id") if column in sample.columns]
    if sort_columns:
        sample = sample.sort_values(sort_columns).reset_index(drop=True)
    raw = sample.to_json(orient="split", date_format="iso", date_unit="ns", double_precision=15, default_handler=str)
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def freeze_market_frame(frame: pd.DataFrame, root: str | Path, *, vendor: str,
                        query_params: dict[str, Any] | None = None,
                        source_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist a complete immutable panel, independent of mutable provider caches.

    Publish the manifest last under an interprocess lock; incomplete directories
    have no valid manifest and can be retried. Readers only use complete manifests.
    """
    snapshot = build_market_frame_snapshot(frame, vendor=vendor, query_params=query_params,
                                           source_metadata=source_metadata)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / snapshot["snapshot_id"]
    with FileLock(str(root / f".{snapshot['snapshot_id']}.lock")):
        if (destination / "manifest.json").exists():
            return load_frozen_market_frame(snapshot["snapshot_id"], root).attrs["data_snapshot"]
        destination.mkdir(exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".market-", suffix=".parquet", dir=destination)
        os.close(fd)
        try:
            stored = frame.copy()
            stored.attrs = {}
            stored.to_parquet(temporary, index=False)
            snapshot.update(replayable=True, files={"market.parquet": _file_hash(Path(temporary))})
            os.replace(temporary, destination / "market.parquet")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        fd, temporary = tempfile.mkstemp(prefix=".manifest-", suffix=".json", dir=destination)
        os.close(fd)
        try:
            Path(temporary).write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
            os.replace(temporary, destination / "manifest.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return load_frozen_market_frame(snapshot["snapshot_id"], root).attrs["data_snapshot"]


def load_frozen_market_frame(snapshot_id: str, root: str | Path) -> pd.DataFrame:
    """Load and verify saved file bytes and logical identity before use."""
    if not re.fullmatch(r"ds_[0-9a-f]{32}", snapshot_id):
        raise ValueError("invalid snapshot_id")
    directory = Path(root) / snapshot_id
    snapshot = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    data_path = directory / "market.parquet"
    if snapshot.get("snapshot_id") != snapshot_id or snapshot.get("files", {}).get("market.parquet") != _file_hash(data_path):
        raise ValueError("snapshot file content hash mismatch")
    frame = pd.read_parquet(data_path)
    if snapshot.get("content_hash") != _frame_content_hash(frame):
        raise ValueError("snapshot logical content hash mismatch")
    metadata = snapshot.get("source_metadata", {})
    attributes = metadata.get("input_attributes", {})
    for name in INPUT_ATTRIBUTES:
        if name in attributes:
            frame.attrs[name] = attributes[name]
    query = dict(snapshot.get("query_params") or {})
    endpoint = query.pop("endpoint", "market_frame")
    verified = build_market_frame_snapshot(frame, vendor=snapshot["vendor"], source_kind=snapshot["source_kind"],
                                           endpoint=endpoint, query_params=query, source_metadata=metadata)
    if verified["snapshot_id"] != snapshot_id:
        raise ValueError("snapshot manifest identity mismatch")
    return attach_data_snapshot(frame, snapshot)


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonical_json_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(v) for v in value]
    if isinstance(value, set):
        return [_canonical_json_value(v) for v in sorted(value, key=str)]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (pd.Index, np.ndarray)):
        return [_canonical_json_value(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _canonical_json_value(value.item())
    return value
