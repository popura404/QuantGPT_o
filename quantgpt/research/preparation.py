"""Verified frozen-panel grants and the research inputs visible in one project."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.data_snapshots import freeze_market_frame, load_frozen_market_frame
from quantgpt.models import ResearchAuditEvent, ResearchProject
from quantgpt.research.contracts import DataInputRef
from quantgpt.research.demo import prepare_offline_demo
from quantgpt.research.hypotheses import ACTION, get_hypothesis, lock_research_registration
from quantgpt.research.projects import ProjectAccessError, require_project_member


def _check_snapshot_path(root: Path, manifest_id: str) -> None:
    if not re.fullmatch(r"ds_[0-9a-f]{32}", manifest_id):
        raise ValueError("INVALID_SNAPSHOT_ID")
    directory = root.resolve() / manifest_id
    for name in ("manifest.json", "market.parquet"):
        if (directory / name).resolve().parent != directory:
            raise ValueError("SNAPSHOT_PATH_OUTSIDE_ROOT")


def _snapshot_summary(root: Path, manifest_id: str) -> dict:
    _check_snapshot_path(root, manifest_id)
    frame = load_frozen_market_frame(manifest_id, root)
    required = {"trade_date", "stock_code", "open", "high", "low", "close", "volume"}
    if frame.empty or not required.issubset(frame):
        raise ValueError(f"RESEARCH_PANEL_REQUIRED: expected populated columns {sorted(required)}")
    dates = pd.DatetimeIndex(pd.to_datetime(frame["trade_date"], errors="coerce"))
    securities = cast(pd.Series, frame["stock_code"])
    if dates.hasnans or securities.isna().any() or frame.duplicated(["trade_date", "stock_code"]).any():
        raise ValueError("INVALID_SECURITY_SESSION_KEYS")
    manifest = frame.attrs["data_snapshot"]
    metadata = manifest.get("source_metadata") or {}
    query = manifest.get("query_params") or {}
    declared_synthetic = metadata.get("synthetic") is True or manifest["vendor"] == "synthetic_offline_fixture"
    return {
        "manifest_id": manifest_id, "content_sha256": manifest["content_hash"].removeprefix("sha256:"),
        "source": manifest["vendor"], "feed": query.get("feed") or metadata.get("feed") or manifest["vendor"],
        "adjustment": frame.attrs.get("adjustment") or metadata.get("adjustment") or "unknown",
        "fields": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        "rows": len(frame), "securities": int(securities.nunique()), "sessions": int(dates.nunique()),
        "start_session": cast(pd.Timestamp, dates.min()).date().isoformat(),
        "end_session": cast(pd.Timestamp, dates.max()).date().isoformat(),
        "snapshot_integrity": "verified", "declared_synthetic": declared_synthetic,
        "external_data_acceptance": "not_run",
        "unverified": ["historical_membership", "corporate_action_coverage", "total_return_benchmark"],
    }


def _with_reference(summary: dict, registered_at: datetime) -> dict:
    observed = registered_at if registered_at.tzinfo else registered_at.replace(tzinfo=timezone.utc)
    fields = {key: summary[key] for key in ("manifest_id", "content_sha256", "source", "feed", "adjustment")}
    ref = DataInputRef(**fields, asof=observed, version_status="verified")
    return {**summary, "data_input": ref.model_dump(mode="json"), "asof_basis": "server_snapshot_registration"}


def _shared_root(public: Path, private: Path) -> Path:
    public, private = public.resolve(), private.resolve()
    if public.is_relative_to(private) or private.is_relative_to(public):
        raise ValueError("SHARED_IMPORT_ROOT_MUST_BE_SEPARATE")
    return public


def _import_shared_panel(manifest_id: str, public: Path, private: Path) -> None:
    public = _shared_root(public, private)
    _check_snapshot_path(public, manifest_id)
    _check_snapshot_path(private, manifest_id)
    if not (public / manifest_id / "manifest.json").is_file():
        raise ProjectAccessError("Snapshot unavailable for import")
    panel = load_frozen_market_frame(manifest_id, public)
    manifest = panel.attrs["data_snapshot"]
    imported = freeze_market_frame(panel, private, vendor=manifest["vendor"], query_params=manifest["query_params"],
                                   source_metadata=manifest.get("source_metadata"))
    if imported["snapshot_id"] != manifest_id:
        raise ValueError("IMPORTED_SNAPSHOT_IDENTITY_CHANGED")


def _catalog_page(public: Path, private: Path, after: str, limit: int) -> dict:
    public = _shared_root(public, private)
    if not public.is_dir():
        raise ValueError("SHARED_IMPORT_CATALOG_UNAVAILABLE")
    candidates = sorted(path.name for path in public.iterdir() if re.fullmatch(r"ds_[0-9a-f]{32}", path.name)
                        and path.name > after)
    items = []
    last = None
    for identity in candidates:
        try:
            _check_snapshot_path(public, identity)
            manifest = json.loads((public / identity / "manifest.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if (not isinstance(manifest, dict) or manifest.get("snapshot_id") != identity
                or not isinstance(manifest.get("field_schema"), dict) or not manifest.get("replayable")):
            continue
        if len(items) == limit:
            return {"configured": True, "snapshots": items, "next_cursor": last}
        items.append({"manifest_id": identity, "source": manifest.get("vendor"),
                      "fields": sorted(manifest["field_schema"]), "rows": manifest.get("row_count"),
                      "start_session": manifest.get("date_min"), "end_session": manifest.get("date_max"),
                      "snapshot_integrity": "verify_on_import"})
        last = identity
    return {"configured": True, "snapshots": items, "next_cursor": None}


async def list_project_imports(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *,
                               snapshot_root: str | Path, after: str = "", limit: int = 25) -> dict:
    """Discover administrator-published manifests without opening the private store."""
    await require_project_member(session, actor_id, project_id)
    if not 1 <= limit <= 100 or (after and not re.fullmatch(r"ds_[0-9a-f]{32}", after)):
        raise ValueError("INVALID_IMPORT_CATALOG_PAGE")
    public = os.environ.get("QUANTGPT_RESEARCH_IMPORT_ROOT")
    if not public:
        return {"configured": False, "snapshots": [], "next_cursor": None}
    return await asyncio.to_thread(_catalog_page, Path(public), Path(snapshot_root), after, limit)


def _grant_query(project_id: uuid.UUID, manifest_id: str):
    return select(ResearchAuditEvent).where(
        ResearchAuditEvent.project_id == project_id, ResearchAuditEvent.action == "snapshot_registered",
        ResearchAuditEvent.payload["snapshot_id"].as_string() == manifest_id,
    ).order_by(ResearchAuditEvent.created_at, ResearchAuditEvent.id)


async def _record_grant(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                        ref: dict, *, asof_basis: str) -> ResearchAuditEvent:
    manifest_id = ref["manifest_id"]
    await lock_research_registration(session, actor_id, project_id)
    previous = (await session.scalars(_grant_query(project_id, manifest_id))).first()
    if previous is None:
        previous = ResearchAuditEvent(id=uuid.uuid5(project_id, "snapshot:" + manifest_id), project_id=project_id,
            actor_id=actor_id, action="snapshot_registered", payload={"snapshot_id": manifest_id, "source": ref["source"],
                                                                    "data_input": ref, "asof_basis": asof_basis})
        session.add(previous)
        await session.flush()
    return previous


def _granted_snapshot(summary: dict, event: ResearchAuditEvent) -> dict:
    recorded = event.payload.get("data_input")
    if recorded is None:
        return _with_reference(summary, event.created_at)
    ref = DataInputRef.model_validate(recorded)
    if (ref.manifest_id != summary["manifest_id"] or ref.content_sha256 != summary["content_sha256"]
            or ref.source != summary["source"] or ref.adjustment != summary["adjustment"]):
        raise ValueError("GRANTED_SNAPSHOT_IDENTITY_CHANGED")
    return {**summary, "feed": ref.feed, "data_input": ref.model_dump(mode="json"), "asof_basis": event.payload["asof_basis"]}


async def prepare_project_demo(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                               *, snapshot_root: str | Path) -> dict:
    """The server generator owns this synthetic fixture, independent of the shared import catalog."""
    await require_project_member(session, actor_id, project_id, write=True)
    payload = await asyncio.to_thread(prepare_offline_demo, snapshot_root)
    ref = payload["config"]["data_inputs"][0]
    grant = await _record_grant(session, actor_id, project_id, ref, asof_basis="synthetic_fixture_version")
    if grant.payload.get("data_input"):
        payload["config"]["data_inputs"] = [grant.payload["data_input"]]
    await session.commit()
    return payload


async def register_project_snapshot(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                                    manifest_id: str, *, snapshot_root: str | Path,
                                    import_root: str | Path | None = None) -> dict:
    """Grant only an administrator-published shared panel or reverify this project's existing grant."""
    await require_project_member(session, actor_id, project_id, write=True)
    if not re.fullmatch(r"ds_[0-9a-f]{32}", manifest_id):
        raise ValueError("INVALID_SNAPSHOT_ID")
    if (await session.scalars(_grant_query(project_id, manifest_id))).first() is None:
        public_root = import_root or os.environ.get("QUANTGPT_RESEARCH_IMPORT_ROOT")
        if not public_root:
            raise ProjectAccessError("Snapshot unavailable for import")
        await asyncio.to_thread(_import_shared_panel, manifest_id, Path(public_root), Path(snapshot_root))
    summary = await asyncio.to_thread(_snapshot_summary, Path(snapshot_root), manifest_id)
    descriptor = _with_reference(summary, datetime.now(timezone.utc))
    previous = await _record_grant(session, actor_id, project_id, descriptor["data_input"], asof_basis=descriptor["asof_basis"])
    result = _granted_snapshot(summary, previous)
    await session.commit()
    return result


async def get_project_preparation(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *,
                                  snapshot_root: str | Path) -> dict:
    """Report the current holdout and verified project inputs without exposing another project's catalog."""
    member = await require_project_member(session, actor_id, project_id)
    project = await session.get(ResearchProject, project_id)
    assert project is not None
    events = (await session.scalars(select(ResearchAuditEvent).where(
        ResearchAuditEvent.project_id == project_id, ResearchAuditEvent.action.in_(("snapshot_registered", ACTION)),
    ).order_by(ResearchAuditEvent.created_at, ResearchAuditEvent.id))).all()
    snapshots: dict[str, dict] = {}
    hypotheses = []
    for event in events:
        if event.action == ACTION:
            hypotheses.append(await get_hypothesis(session, actor_id, project_id, event.payload["definition_hash"]))
            continue
        identity = event.payload["snapshot_id"]
        if identity in snapshots:
            continue
        try:
            summary = await asyncio.to_thread(_snapshot_summary, Path(snapshot_root), identity)
            snapshots[identity] = _granted_snapshot(summary, event)
        except (ValueError, OSError) as exc:
            snapshots[identity] = {"manifest_id": identity, "snapshot_integrity": "blocked",
                                   "reason": type(exc).__name__, "external_data_acceptance": "not_run"}
    return {"project_id": str(project_id), "name": project.name, "market": project.market, "role": member.role,
            "holdout": project.config.get("holdout"), "snapshots": list(snapshots.values()), "hypotheses": hypotheses}
