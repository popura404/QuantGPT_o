"""Bind a frozen panel to evidence published by the deployment operator."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PublishedEvidence:
    revision: str
    payload: dict | None = None
    error: str | None = None


def read_published_evidence(manifest_id: str | None, *, include_payload: bool = False) -> PublishedEvidence:
    """Inputs to this catalog are granted snapshot IDs, never client paths or URLs."""
    root_name = os.environ.get("QUANTGPT_RESEARCH_EVIDENCE_ROOT")
    if not root_name:
        return PublishedEvidence("not_supplied")
    if not manifest_id or not re.fullmatch(r"ds_[0-9a-f]{32}", manifest_id):
        return PublishedEvidence("invalid_id", error="INVALID_SNAPSHOT_ID")
    try:
        root = Path(root_name).resolve()
        path = root / manifest_id / "evidence.json"
        if path.resolve() != path:
            return PublishedEvidence("invalid_path", error="EXTERNAL_EVIDENCE_PATH_INVALID")
        with path.open("rb") as handle:
            if not include_payload:
                digest = hashlib.sha256()
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
                return PublishedEvidence(digest.hexdigest())
            raw = handle.read()
    except FileNotFoundError:
        return PublishedEvidence("not_supplied")
    except (OSError, RuntimeError):
        return PublishedEvidence("unreadable", error="EXTERNAL_EVIDENCE_UNREADABLE")
    revision = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        return PublishedEvidence(revision, error="EXTERNAL_EVIDENCE_INVALID_JSON")
    return PublishedEvidence(revision, payload)
