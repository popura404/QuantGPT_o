"""Factor values computation endpoint."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user
from ..factor_values import compute_factor_values_payload
from ..models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/factor_values", tags=["factor_values"])


class FactorValuesRequest(BaseModel):
    expression: str
    universe: str = "csi500"
    start_date: str = ""
    end_date: str = ""


@router.post("")
async def compute_factor_values(
    req: FactorValuesRequest,
    user: User = Depends(get_current_user),
):
    try:
        return await asyncio.to_thread(
            compute_factor_values_payload,
            req.expression,
            req.universe,
            req.start_date,
            req.end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Factor values computation failed")
        raise HTTPException(status_code=400, detail=f"Factor values computation failed: {exc}")
