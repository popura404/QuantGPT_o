"""MCP adapters for the project preparation and comparison services."""

from __future__ import annotations

import json
import uuid

from mcp.server.fastmcp import FastMCP

from quantgpt.db import _get_session_factory
from quantgpt.research.comparison import compare_research_strategies
from quantgpt.research.contracts import EvaluationRef, FactorDefinitionV1
from quantgpt.research.evaluations import register_holdout
from quantgpt.research.hypotheses import register_hypothesis
from quantgpt.research.preparation import (
    get_project_preparation,
    list_project_imports,
    prepare_project_demo,
    register_project_snapshot,
)
from quantgpt.research.projects import ProjectAccessError, create_project, resolve_mcp_actor
from quantgpt.research.runtime import snapshot_root


async def _call(operation, *args, **kwargs) -> str:
    try:
        async with _get_session_factory()() as db:
            result = await operation(db, resolve_mcp_actor(), *args, **kwargs)
            await db.commit()
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
    except ProjectAccessError:
        return json.dumps({"error_code": "PROJECT_OR_RESOURCE_UNAVAILABLE"})
    except (ValueError, OSError) as exc:
        return json.dumps({"error_code": "RESEARCH_PREPARATION_BLOCKED", "message": str(exc)}, ensure_ascii=False)


def register_preparation_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def create_research_project(name: str, market: str = "us_equity") -> str:
        """Create a shared research project owned by the current authenticated principal."""
        async def create(db, actor):
            if len(name.strip()) > 200 or not 1 <= len(market.strip()) <= 60:
                raise ValueError("Invalid project name or market")
            project = await create_project(db, actor, name=name, market=market.strip())
            return {"id": str(project.id), "name": project.name, "market": project.market}
        return await _call(create)

    @mcp.tool()
    async def get_research_preparation(project_id: uuid.UUID) -> str:
        """Read project holdout, registered hypotheses and verified fields/coverage of its authorized snapshots."""
        return await _call(get_project_preparation, project_id, snapshot_root=snapshot_root())

    @mcp.tool()
    async def list_research_snapshot_imports(project_id: uuid.UUID, after: str = "", limit: int = 25) -> str:
        """List candidate manifests in the administrator's shared catalog. Use next_cursor as after to continue."""
        return await _call(list_project_imports, project_id, snapshot_root=snapshot_root(), after=after, limit=limit)

    @mcp.tool()
    async def register_research_snapshot(project_id: uuid.UUID, manifest_id: str) -> str:
        """Import a server-published shared manifest, or verify an existing project grant. Returns the frozen data input."""
        return await _call(register_project_snapshot, project_id, manifest_id, snapshot_root=snapshot_root())

    @mcp.tool()
    async def prepare_research_demo(project_id: uuid.UUID) -> str:
        """Generate an explicitly synthetic offline panel, evaluation config and strategy template in a project."""
        return await _call(prepare_project_demo, project_id, snapshot_root=snapshot_root())

    @mcp.tool()
    async def register_research_holdout(project_id: uuid.UUID, start: str, end: str) -> str:
        """Preregister the project's final dates before any trial. Dates use YYYY-MM-DD."""
        return await _call(register_holdout, project_id, start=start, end=end)

    @mcp.tool()
    async def register_research_hypothesis(project_id: uuid.UUID, definition: FactorDefinitionV1,
                                           economic_hypothesis: str, frozen_baseline: EvaluationRef | None = None) -> str:
        """Freeze a factor definition's economic rationale and optional factor comparator before its first evaluation."""
        return await _call(register_hypothesis, project_id, definition, economic_hypothesis=economic_hypothesis,
                           frozen_baseline=frozen_baseline)

    @mcp.tool()
    async def compare_research_strategies_to_baseline(project_id: uuid.UUID, round_id: uuid.UUID,
                                                       augmented_run_id: uuid.UUID) -> str:
        """Compare a saved augmented selection strategy with its round's frozen baseline using aligned net returns."""
        return await _call(compare_research_strategies, project_id, round_id, augmented_run_id)
