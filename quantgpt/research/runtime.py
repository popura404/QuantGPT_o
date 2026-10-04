"""Server-owned runtime identity and storage locations for research runs."""

import hashlib
import json
import os
import platform
from importlib import metadata
from pathlib import Path

from quantgpt.research.contracts import EngineIdentity, EvaluationConfigV1

NUMERICAL_DISTRIBUTIONS = (
    "numpy", "pandas", "scipy", "pyarrow", "quantstats", "statsmodels",
    "pydantic", "pydantic-core", "exchange-calendars", "python-dateutil", "tzdata",
)


def numerical_runtime() -> dict:
    """Fingerprint installed dependencies as well as the declared lockfile."""
    versions = {}
    for name in NUMERICAL_DISTRIBUTIONS:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    lock_name = "windows-py312.lock" if os.name == "nt" else "linux-py312.lock"
    lock = Path(__file__).resolve().parents[2] / "requirements" / lock_name
    return {"schema_version": "numerical_runtime/v1", "dependencies": versions,
            "implementation": platform.python_implementation(), "system": platform.system(),
            "machine": platform.machine(), "lock_name": lock_name,
            "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else "unavailable"}


def snapshot_root() -> Path:
    return Path(os.environ.get("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", "data/research-snapshots")).resolve()


def local_engine_identity() -> EngineIdentity:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    # Include all numerical and data semantics, including uncommitted source.
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    digest.update(json.dumps(numerical_runtime(), sort_keys=True, separators=(",", ":")).encode())
    return EngineIdentity(engine="python", version=platform.python_version(),
                          code_version="sha256:" + digest.hexdigest(), conformance="unverified")


def normalize_local_config(config: EvaluationConfigV1) -> EvaluationConfigV1:
    if config.backend != "local":
        raise ValueError("Local runner requires a local backend")
    payload = config.model_dump(mode="json")
    payload["engine"] = local_engine_identity().model_dump(mode="json")
    return EvaluationConfigV1.model_validate(payload)
