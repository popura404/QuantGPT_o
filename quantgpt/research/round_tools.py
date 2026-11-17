"""Agent-facing tools for coordinated selection and a single frozen final run."""

from __future__ import annotations

import json
import uuid
from typing import Literal

from mcp.server.fastmcp import FastMCP

from quantgpt.db import _get_session_factory
from quantgpt.research.projects import ProjectAccessError, resolve_mcp_actor
from quantgpt.task_executor import start_task_dispatcher


async def _run(operation, project_id: uuid.UUID, *, dispatch: bool = False, **params) -> str:
    try:
        async with _get_session_factory()() as session:
            result = await operation(session, resolve_mcp_actor(), project_id, **params)
            await session.commit()
        if dispatch and "task_id" in result:
            start_task_dispatcher()
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
    except ProjectAccessError:
        return json.dumps({"error_code": "ROUND_OR_CREDENTIAL_UNAVAILABLE"})
    except FileNotFoundError:
        return json.dumps({"error_code": "RESEARCH_ROUND_BLOCKED", "message": "RESEARCH_INPUT_FILE_UNAVAILABLE"})
    except ValueError as exc:
        return json.dumps({"error_code": "RESEARCH_ROUND_BLOCKED", "message": str(exc)}, ensure_ascii=False)


def register_round_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def plan_research_windows(project_id: uuid.UUID, config: dict, as_of: str | None = None) -> str:
        """Preview 756 selection and 252 holdout sessions on the registered snapshot. Omit dates from the config template. as_of caps the last eligible session. Returns coverage shortfalls when history is insufficient."""
        from quantgpt.research.window_policy import plan_project_windows

        return await _run(plan_project_windows, project_id, config=config, as_of=as_of)

    @mcp.tool()
    async def create_research_round(project_id: uuid.UUID, config: dict, directions: list[dict],
                                    holdout_start: str | None = None, holdout_end: str | None = None,
                                    baseline_run_id: uuid.UUID | None = None, coordinator_key: str | None = None,
                                    as_of: str | None = None) -> str:
        """Freeze inputs, windows and directions. Supply both selection and holdout dates, or omit all four to use the default window plan. as_of limits automatic planning."""
        from quantgpt.research.rounds import create_round

        return await _run(create_round, project_id, config=config, holdout_start=holdout_start, holdout_end=holdout_end,
                          directions=directions, baseline_run_id=baseline_run_id, coordinator_key=coordinator_key,
                          as_of=as_of)

    @mcp.tool()
    async def get_research_round(project_id: uuid.UUID, round_id: uuid.UUID) -> str:
        """Read the round, its directions, task states and candidate evaluation IDs after checking project membership."""
        from quantgpt.research.rounds import describe_round

        return await _run(describe_round, project_id, round_id=round_id)

    @mcp.tool()
    async def check_research_factor_novelty(project_id: uuid.UUID, expressions: list[str]) -> str:
        """Before mining, compare proposals with accessible factor libraries, historical evaluations and active project tasks. Exact formula matches and parameter-family hints include source IDs."""
        from quantgpt.research.novelty import check_factor_novelty

        return await _run(check_factor_novelty, project_id, expressions=expressions)

    @mcp.tool()
    async def submit_research_direction(project_id: uuid.UUID, round_id: uuid.UUID, direction_id: uuid.UUID,
                                        direction_key: str, definitions: list[dict], idempotency_key: str | None = None,
                                        direction: Literal["higher_is_better", "lower_is_better"] | None = None,
                                        parent_evaluation_id: str | None = None, only_new: bool = False) -> str:
        """Mine new ideas with only_new=true: atomically check the batch and return new_indices if any formula is known. For deliberate replication, omit only_new to share exact evaluation work and credit this direction."""
        from quantgpt.research.rounds import submit_direction

        return await _run(submit_direction, project_id, dispatch=True, round_id=round_id, direction_id=direction_id,
                          direction_key=direction_key, definitions=definitions, idempotency_key=idempotency_key,
                          direction=direction, parent_evaluation_id=parent_evaluation_id, only_new=only_new)

    @mcp.tool()
    async def cancel_research_round_task(project_id: uuid.UUID, round_id: uuid.UUID, task_id: str,
                                         direction_id: uuid.UUID | None = None, direction_key: str | None = None,
                                         coordinator_key: str | None = None) -> str:
        """Cancel a task with its own direction credential or the coordinator key. Resume a frozen final with finalize_research_round."""
        from quantgpt.research.rounds import cancel_round_task

        return await _run(cancel_round_task, project_id, round_id=round_id, task_id=task_id, direction_id=direction_id,
                          direction_key=direction_key, coordinator_key=coordinator_key)

    @mcp.tool()
    async def finish_research_direction(project_id: uuid.UUID, round_id: uuid.UUID, direction_id: uuid.UUID,
                                        direction_key: str | None = None, coordinator_key: str | None = None) -> str:
        """Close a direction's submissions with its own credential or its coordinator's credential."""
        from quantgpt.research.rounds import finish_direction

        return await _run(finish_direction, project_id, round_id=round_id, direction_id=direction_id,
                          direction_key=direction_key, coordinator_key=coordinator_key)

    @mcp.tool()
    async def submit_round_strategy(project_id: uuid.UUID, round_id: uuid.UUID, coordinator_key: str,
                                    spec: dict) -> str:
        """Queue a coordinator's selection strategy using component references from this round and its frozen baseline."""
        from quantgpt.research.rounds import submit_strategy

        return await _run(submit_strategy, project_id, dispatch=True, round_id=round_id, coordinator_key=coordinator_key, spec=spec)

    @mcp.tool()
    async def finalize_research_round(project_id: uuid.UUID, round_id: uuid.UUID, coordinator_key: str,
                                      selection_run_id: uuid.UUID) -> str:
        """Freeze the selected combination and queue its single project holdout evaluation. Poll the returned task ID."""
        from quantgpt.research.round_final import finalize_round

        return await _run(finalize_round, project_id, dispatch=True, round_id=round_id, coordinator_key=coordinator_key,
                          selection_run_id=selection_run_id)

    @mcp.tool()
    async def import_research_iteration(project_id: uuid.UUID, round_id: uuid.UUID, direction_id: uuid.UUID,
                                        direction_key: str, task_id: str) -> str:
        """Attach all observed candidate records from a saved iteration task to this direction's research history."""
        from quantgpt.research.rounds import import_iteration

        return await _run(import_iteration, project_id, round_id=round_id, direction_id=direction_id,
                          direction_key=direction_key, task_id=task_id)
