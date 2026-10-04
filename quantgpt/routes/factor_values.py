"""Factor values computation endpoint."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user
from ..factor_values import compute_factor_values_payload
from ..models import User
from ..us_data.contracts import DataCapabilityError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/factor_values", tags=["factor_values"])


class FactorValuesRequest(BaseModel):
    expression: str
    universe: str = "csi500"
    start_date: str = ""
    end_date: str = ""
    market: str = "a_share"
    backend: str = "local"
    allow_remote_fetch: bool = True


@router.post("")
async def compute_factor_values(
    req: FactorValuesRequest,
    user: User = Depends(get_current_user),
):
    try:
        dispatch: dict[str, Any] = {}
        if req.market != "a_share" or req.backend != "local" or not req.allow_remote_fetch:
            dispatch = {"market": req.market, "backend": req.backend, "allow_remote_fetch": req.allow_remote_fetch}
        return await asyncio.to_thread(
            compute_factor_values_payload,
            req.expression,
            req.universe,
            req.start_date,
            req.end_date,
            **dispatch,
        )
    except DataCapabilityError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Factor values computation failed")
        raise HTTPException(status_code=400, detail=f"Factor values computation failed: {exc}")
