"""Authenticated research projects and immutable evaluation references."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.auth import get_current_user
from quantgpt.db import get_db
from quantgpt.models import ResearchAuditEvent, User
from quantgpt.research.contracts import EvaluationConfigV1, EvaluationRef, FactorDefinitionV1
from quantgpt.research.evaluations import (
    evaluate_factor_batch,
    evaluation_summary,
    get_evaluation,
    read_artifact,
    register_holdout,
)
from quantgpt.research.portfolio import PortfolioOptimizationConfig, optimize_signal_reference
from quantgpt.research.projects import (
    ProjectAccessError,
    create_project,
    list_projects,
    migrate_personal_favorites,
    require_project_member,
    set_project_member,
)
from quantgpt.research.runtime import snapshot_root
from quantgpt.research.strategies import export_strategy_run, get_strategy_run, run_strategy_evaluation
from quantgpt.research.validation import evaluate_profile
from quantgpt.strategy.spec import StrategySpecV2

router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.post("/projects/{project_id}/offline-demo")
async def prepare_demo(project_id: uuid.UUID, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    import asyncio

    from quantgpt.research.demo import prepare_offline_demo

    try:
        await require_project_member(db, str(user.id), project_id, write=True)
        payload = await asyncio.to_thread(prepare_offline_demo, snapshot_root())
        db.add(ResearchAuditEvent(project_id=project_id, actor_id=uuid.UUID(str(user.id)),
            action="snapshot_registered", payload={"snapshot_id": payload["config"]["data_inputs"][0]["manifest_id"],
                                                   "source": "synthetic_offline_fixture"}))
        await db.commit()
        return payload
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    market: str = "us_equity"


class MemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID
    role: str | None = "researcher"


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definitions: list[FactorDefinitionV1] = Field(min_length=1, max_length=100)
    config: EvaluationConfigV1


class HoldoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str
    end: str


class HypothesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: FactorDefinitionV1
    economic_hypothesis: str = Field(min_length=1, max_length=10000)
    frozen_baseline: EvaluationRef | None = None


@router.post("/projects/{project_id}/hypotheses")
async def post_hypothesis(project_id: uuid.UUID, req: HypothesisRequest,
                            user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from quantgpt.research.hypotheses import register_hypothesis

    try:
        result = await register_hypothesis(db, uuid.UUID(str(user.id)), project_id, req.definition,
            economic_hypothesis=req.economic_hypothesis, frozen_baseline=req.frozen_baseline)
        await db.commit()
        return result
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class StrategyRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: StrategySpecV2
    config: EvaluationConfigV1


class ResearchTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: dict
    idempotency_key: str | None = Field(None, min_length=1, max_length=160)


class PortfolioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_ref: dict
    config: PortfolioOptimizationConfig


@router.post("/projects/{project_id}/portfolio-optimizations")
async def optimize_portfolio_request(project_id: uuid.UUID, req: PortfolioRequest,
                                     user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await optimize_signal_reference(db, uuid.UUID(str(user.id)), project_id, req.signal_ref, req.config,
                                                snapshot_root=snapshot_root())
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/tasks", status_code=202)
async def submit_research_task(project_id: uuid.UUID, req: ResearchTaskRequest,
                                user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from quantgpt.research.jobs import submit_research_request
    from quantgpt.task_store import TaskConflictError

    try:
        return await submit_research_request(db, uuid.UUID(str(user.id)), project_id, req.request, req.idempotency_key)
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except TaskConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects")
async def get_projects(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    projects = await list_projects(db, uuid.UUID(str(user.id)))
    return {"projects": [{"id": str(item.id), "name": item.name, "market": item.market,
                           "owner_user_id": str(item.owner_user_id)} for item in projects]}


@router.post("/projects", status_code=201)
async def post_project(req: ProjectRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    project = await create_project(db, uuid.UUID(str(user.id)), name=req.name, market=req.market)
    await db.commit()
    return {"id": str(project.id), "name": project.name, "market": project.market}


@router.put("/projects/{project_id}/members")
async def put_member(project_id: uuid.UUID, req: MemberRequest, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    try:
        await set_project_member(db, uuid.UUID(str(user.id)), project_id, req.user_id, req.role)
        await db.commit()
        return {"updated": True}
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/favorites-migration")
async def migrate_favorites(project_id: uuid.UUID, dry_run: bool = True, user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    try:
        payload = await migrate_personal_favorites(db, uuid.UUID(str(user.id)), project_id, dry_run=dry_run)
        await db.commit()
        return payload
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/projects/{project_id}/holdout")
async def post_holdout(project_id: uuid.UUID, req: HoldoutRequest, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    try:
        payload = await register_holdout(db, uuid.UUID(str(user.id)), project_id, start=req.start, end=req.end)
        await db.commit()
        return payload
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/evaluations")
async def post_evaluations(project_id: uuid.UUID, req: EvaluationRequest, user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    try:
        if req.config.backend == "wq":
            from quantgpt.research.jobs import execute_research_request

            return await execute_research_request(db, uuid.UUID(str(user.id)), project_id,
                {"kind": "factor", "definitions": [item.model_dump(mode="json") for item in req.definitions],
                 "config": req.config.model_dump(mode="json")})
        return await evaluate_factor_batch(db, uuid.UUID(str(user.id)), project_id, req.definitions, req.config,
                                           snapshot_root=snapshot_root())
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects/{project_id}/evaluations/{evaluation_id}")
async def read_evaluation(project_id: uuid.UUID, evaluation_id: str, user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    try:
        row = await get_evaluation(db, uuid.UUID(str(user.id)), project_id, evaluation_id)
        return {**evaluation_summary(row), "frozen_config": row.evaluation_config}
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/projects/{project_id}/evaluations/{evaluation_id}/validation")
async def read_validation(project_id: uuid.UUID, evaluation_id: str, profile: str = "local_factor",
                           user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await evaluate_profile(db, uuid.UUID(str(user.id)), project_id, evaluation_id, profile=profile)
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects/{project_id}/artifacts/{artifact_id}")
async def read_research_artifact(project_id: uuid.UUID, artifact_id: uuid.UUID,
                                  user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await read_artifact(db, uuid.UUID(str(user.id)), project_id, artifact_id)
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/strategy-runs")
async def post_strategy_run(project_id: uuid.UUID, req: StrategyRunRequest,
                              user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await run_strategy_evaluation(db, uuid.UUID(str(user.id)), project_id, req.spec, req.config,
                                              snapshot_root=snapshot_root())
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects/{project_id}/strategy-runs/{run_id}")
async def read_strategy_run(project_id: uuid.UUID, run_id: uuid.UUID,
                             user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return (await get_strategy_run(db, uuid.UUID(str(user.id)), project_id, run_id)).result
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/strategy-runs/{run_id}/export")
async def export_research_strategy(project_id: uuid.UUID, run_id: uuid.UUID,
                                    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        return await export_strategy_run(db, uuid.UUID(str(user.id)), project_id, run_id)
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/capabilities")
async def research_capabilities(user: User = Depends(get_current_user)):
    from quantgpt.research.wq_backend import capability_report
    from quantgpt.strategy.adapters import list_markets

    return {"local_markets": list_markets(), "wq": capability_report(),
            "synthetic_demo": True, "paid_provider_interface": "quantgpt.us_data.contracts.USDataProvider"}


@router.post("/projects/{project_id}/evaluations/{evaluation_id}/reconcile")
async def reconcile_remote_evaluation(project_id: uuid.UUID, evaluation_id: str,
                                        user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    import asyncio

    from quantgpt.research.wq_backend import reconcile_wq_evaluation
    from quantgpt.wq_brain_client import get_client

    try:
        await require_project_member(db, str(user.id), project_id, write=True)
        client = get_client()
        if not await asyncio.to_thread(client.authenticate):
            raise ValueError("WQ_AUTHENTICATION_FAILED")
        return await reconcile_wq_evaluation(db, uuid.UUID(str(user.id)), project_id, evaluation_id, client=client)
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/evaluations/{evaluation_id}/cancel")
async def cancel_remote_wait(project_id: uuid.UUID, evaluation_id: str,
                               user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from quantgpt.research.wq_backend import cancel_wq_evaluation

    try:
        return await cancel_wq_evaluation(db, uuid.UUID(str(user.id)), project_id, evaluation_id)
    except ProjectAccessError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
