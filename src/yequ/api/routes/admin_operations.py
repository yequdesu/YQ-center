"""Admin endpoints for Center Operations."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.services.operation_service import OperationService

router = APIRouter(prefix="/admin/operations", tags=["admin"])


class CancelOperationRequest(BaseModel):
    reason: str = Field(default="operation_cancelled")


@router.get("/{operation_id}")
async def get_operation(
    operation_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> dict[str, object]:
    try:
        return await OperationService(db).status(operation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{operation_id}/cancel")
async def cancel_operation(
    operation_id: str,
    body: CancelOperationRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> dict[str, object]:
    try:
        return await OperationService(db).cancel(operation_id, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

