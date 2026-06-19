"""YQP Node Protocol — single POST /yqp/ endpoint dispatching on message_type."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_db, get_settings
from yequ.config import Settings
from yequ.protocol import MessageType
from yequ.protocol.envelope import YqpEnvelope
from yequ.protocol.errors import ErrorCode, YqpError
from yequ.services.message_dedup import get_dedup
from yequ.services.node_auth import authenticate_node, verify_node_id_binding
from yequ.services.node_service import (
    handle_heartbeat,
    handle_hello,
    handle_register_capabilities,
    handle_signal_report,
)

router = APIRouter(prefix="/yqp", tags=["yqp"])


def _response_type(request_type: MessageType) -> str:
    """Map request message_type to response message_type."""
    response_map: dict[MessageType, str] = {
        MessageType.NODE_HELLO: MessageType.NODE_ACCEPTED,
        MessageType.NODE_REGISTER_CAPABILITIES: MessageType.REGISTRY_ACCEPTED,
        MessageType.NODE_HEARTBEAT: MessageType.NODE_HEARTBEAT,
        MessageType.SIGNAL_REPORT: MessageType.SIGNAL_REPORT,
        MessageType.JOB_POLL: MessageType.JOB_AVAILABLE,
        MessageType.JOB_ACCEPTED: MessageType.JOB_ACCEPTED,
        MessageType.JOB_FINISHED: MessageType.JOB_FINISHED,
        MessageType.JOB_LEASE_RENEW: MessageType.JOB_LEASE_ACCEPTED,
        MessageType.NODE_RECONCILE_JOBS: MessageType.JOB_RECONCILIATION,
    }
    return response_map.get(request_type, MessageType.ERROR)


@router.post("/")
async def yqp_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Main YQP protocol endpoint.

    Accepts a YqpEnvelope, validates auth/dedup/timestamp,
    dispatches to the appropriate handler based on message_type.
    """
    # 1. Parse envelope
    try:
        body = await request.json()
        envelope = YqpEnvelope.model_validate(body)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message=f"Invalid YQP envelope: {e}",
            ).model_dump(),
        ) from e

    # 2. Authenticate node from Authorization header
    auth_header = request.headers.get("Authorization")
    node = await authenticate_node(db, auth_header)

    # 3. Verify node_id binding
    if envelope.node_id:
        await verify_node_id_binding(node, envelope.node_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message="node_id is required in envelope for Node messages",
            ).model_dump(),
        )

    # 4. Message dedup
    dedup = get_dedup()
    if not dedup.check_and_record(envelope.message_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.DUPLICATE_MESSAGE,
                message=f"Duplicate message_id: {envelope.message_id}",
            ).model_dump(),
        )

    # 5. Timestamp validation
    now = datetime.now(UTC)
    msg_time = envelope.timestamp
    # Handle both aware and naive datetimes
    if msg_time.tzinfo is None:
        msg_time = msg_time.replace(tzinfo=UTC)
    skew = abs((msg_time - now).total_seconds())
    if skew > settings.allowed_timestamp_skew_sec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.TIMESTAMP_OUT_OF_RANGE,
                message=(
                    f"Timestamp skew {skew:.1f}s exceeds "
                    f"allowed {settings.allowed_timestamp_skew_sec}s"
                ),
            ).model_dump(),
        )

    # 6. Dispatch to handler based on message_type
    msg_type = envelope.message_type
    if msg_type == MessageType.NODE_HELLO:
        response_payload = await handle_hello(db, node, envelope.payload, settings)
    elif msg_type == MessageType.NODE_HEARTBEAT:
        response_payload = await handle_heartbeat(db, node, envelope.payload, settings)
    elif msg_type == MessageType.NODE_REGISTER_CAPABILITIES:
        response_payload = await handle_register_capabilities(
            db, node, envelope.payload, settings
        )
    elif msg_type == MessageType.SIGNAL_REPORT:
        response_payload = await handle_signal_report(
            db, node, envelope.payload, settings
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message=f"Unsupported message_type: {msg_type}",
            ).model_dump(),
        )

    # 7. Return response envelope
    return {
        "yqp_version": "0.1",
        "message_id": envelope.message_id,
        "message_type": _response_type(msg_type),
        "trace_id": envelope.trace_id,
        "timestamp": now.isoformat(),
        "payload": response_payload,
    }
