"""Admin endpoints for Node provisioning."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.models.node import Node
from yequ.protocol import NodeStatus
from yequ.services.node_auth import hash_token

router = APIRouter(prefix="/admin", tags=["admin"])


class ProvisionNodeRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=128)
    node_name: str = Field(..., min_length=1, max_length=256)
    token: str = Field(..., min_length=8, description="Plaintext token for this node")
    role: str = Field(default="compute")
    locality: str = Field(default="local")


class ProvisionNodeResponse(BaseModel):
    node_id: str
    node_name: str
    status: str
    message: str


@router.post(
    "/nodes",
    response_model=ProvisionNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision_node(
    body: ProvisionNodeRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> ProvisionNodeResponse:
    """Pre-provision a Node in the system."""
    result = await db.execute(select(Node).where(Node.node_id == body.node_id))
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Node {body.node_id!r} already exists",
        )

    node = Node(
        node_id=body.node_id,
        node_name=body.node_name,
        token_hash=hash_token(body.token),
        role=body.role,
        locality=body.locality,
        status=NodeStatus.PROVISIONED,
    )
    db.add(node)
    await db.commit()

    return ProvisionNodeResponse(
        node_id=body.node_id,
        node_name=body.node_name,
        status=NodeStatus.PROVISIONED,
        message="Node provisioned. Use the token to authenticate via node.hello.",
    )
