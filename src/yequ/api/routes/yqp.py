"""YQP Node Protocol — single POST /yqp/ endpoint dispatching on message_type."""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_db, get_settings
from yequ.config import Settings
from yequ.logconfig import get_logger
from yequ.protocol import MessageType
from yequ.protocol.envelope import YqpEnvelope
from yequ.protocol.errors import ErrorCode, YqpError
from yequ.services.artifact_service import resolve_download
from yequ.services.message_dedup import check_and_record_message
from yequ.services.node_auth import authenticate_node, verify_node_id_binding
from yequ.services.node_service import (
    handle_artifact_upload,
    handle_heartbeat,
    handle_hello,
    handle_job_accepted,
    handle_job_cancel,
    handle_job_event,
    handle_job_finished,
    handle_job_lease_renew,
    handle_job_poll,
    handle_reconcile_jobs,
    handle_register_capabilities,
    handle_signal_report,
)

router = APIRouter(prefix="/yqp", tags=["yqp"])
log = get_logger(__name__)


@router.get("/artifacts/{artifact_id}/download")
async def yqp_artifact_download(
    artifact_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Allow authenticated Nodes to download Center artifacts.

    This endpoint is intentionally separate from the JSON YQP envelope path:
    artifact bytes should not be tunneled through LLM/tool JSON payloads, and
    Nodes must not require an admin token to materialize Center-owned blobs.
    """

    node = await authenticate_node(db, request.headers.get("Authorization"))
    try:
        download = await resolve_download(db, artifact_id, settings=settings)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = download.artifact.title or download.artifact.artifact_id
    return FileResponse(
        download.path,
        media_type=download.blob.content_type or "application/octet-stream",
        filename=filename,
        headers={
            "X-YeQu-Artifact-Id": download.artifact.artifact_id,
            "X-YeQu-Artifact-Sha256": download.blob.sha256,
            "X-YeQu-Artifact-Size": str(download.blob.size_bytes),
            "X-YeQu-Node-Id": node.node_id,
        },
    )


def _response_type(
    request_type: MessageType,
    response_payload: dict[str, object] | None = None,
) -> str:
    """Map request message_type to response message_type."""
    if (
        request_type == MessageType.JOB_POLL
        and response_payload is not None
        and response_payload.get("jobs") == []
    ):
        return MessageType.JOB_EMPTY

    response_map: dict[MessageType, str] = {
        MessageType.NODE_HELLO: MessageType.NODE_ACCEPTED,
        MessageType.NODE_REGISTER_CAPABILITIES: MessageType.REGISTRY_ACCEPTED,
        MessageType.NODE_HEARTBEAT: MessageType.NODE_HEARTBEAT,
        MessageType.SIGNAL_REPORT: MessageType.SIGNAL_REPORT,
        MessageType.ARTIFACT_UPLOAD: MessageType.ARTIFACT_ACCEPTED,
        MessageType.JOB_POLL: MessageType.JOB_AVAILABLE,
        MessageType.JOB_ACCEPTED: MessageType.JOB_ACCEPTED,
        MessageType.JOB_FINISHED: MessageType.JOB_FINISHED,
        MessageType.JOB_LEASE_RENEW: MessageType.JOB_LEASE_ACCEPTED,
        MessageType.JOB_EVENT: MessageType.JOB_EVENT,
        MessageType.JOB_CANCEL: MessageType.JOB_CANCEL,
        MessageType.NODE_RECONCILE_JOBS: MessageType.JOB_RECONCILIATION,
    }
    return response_map.get(request_type, MessageType.ERROR)


@router.post("", include_in_schema=False)
@router.post("/")
async def yqp_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Main YQP protocol endpoint.

    Accepts a YqpEnvelope, validates auth/dedup/timestamp,
    dispatches to the appropriate handler based on message_type.
    """
    started = time.perf_counter()
    previous = started

    def log_stage(
        stage: str,
        *,
        envelope: YqpEnvelope | None = None,
        node_id: str | None = None,
    ) -> None:
        nonlocal previous
        now_perf = time.perf_counter()
        log.debug(
            "yqp.stage",
            stage=stage,
            path=request.url.path,
            message_type=str(envelope.message_type) if envelope else None,
            message_id=envelope.message_id if envelope else None,
            trace_id=envelope.trace_id if envelope else None,
            envelope_node_id=envelope.node_id if envelope else None,
            node_id=node_id,
            elapsed_ms=round((now_perf - started) * 1000, 2),
            stage_ms=round((now_perf - previous) * 1000, 2),
        )
        previous = now_perf

    # 1. Parse envelope
    try:
        body = await request.json()
        envelope = YqpEnvelope.model_validate(body)
        log_stage("parsed", envelope=envelope)
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
    authenticated_node_id = node.node_id
    log_stage("authenticated", envelope=envelope, node_id=authenticated_node_id)

    # 3. Verify node_id binding
    if envelope.node_id:
        await verify_node_id_binding(node, envelope.node_id)
        log_stage("node_binding_verified", envelope=envelope, node_id=authenticated_node_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message="node_id is required in envelope for Node messages",
            ).model_dump(),
        )

    # 4. Timestamp validation
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
    log_stage("timestamp_validated", envelope=envelope, node_id=authenticated_node_id)

    # 5. Message dedup. Commit immediately so dedup INSERT/cleanup DELETE locks
    # are not held while the YQP handler performs node/job work.
    if not await check_and_record_message(
        db,
        message_id=envelope.message_id,
        node_id=authenticated_node_id,
        message_type=str(envelope.message_type),
        trace_id=envelope.trace_id,
        ttl_sec=settings.message_dedup_ttl_sec,
    ):
        log_stage("dedup_duplicate", envelope=envelope, node_id=authenticated_node_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.DUPLICATE_MESSAGE,
                message=f"Duplicate message_id: {envelope.message_id}",
            ).model_dump(),
        )
    await db.commit()
    log_stage("dedup_recorded", envelope=envelope, node_id=authenticated_node_id)

    # 6. Dispatch to handler based on message_type
    msg_type = envelope.message_type
    if msg_type == MessageType.NODE_HELLO:
        response_payload = await handle_hello(db, node, envelope.payload, settings)
    elif msg_type == MessageType.NODE_HEARTBEAT:
        response_payload = await handle_heartbeat(db, node, envelope.payload, settings)
    elif msg_type == MessageType.NODE_REGISTER_CAPABILITIES:
        response_payload = await handle_register_capabilities(db, node, envelope.payload, settings)
    elif msg_type == MessageType.SIGNAL_REPORT:
        response_payload = await handle_signal_report(db, node, envelope.payload, settings)
    elif msg_type == MessageType.ARTIFACT_UPLOAD:
        response_payload = await handle_artifact_upload(db, node, envelope.payload, settings)
    elif msg_type == MessageType.JOB_POLL:
        response_payload = await handle_job_poll(db, node, envelope.payload, settings)
    elif msg_type == MessageType.JOB_ACCEPTED:
        response_payload = await handle_job_accepted(db, node, envelope.payload, settings)
    elif msg_type == MessageType.JOB_FINISHED:
        response_payload = await handle_job_finished(db, node, envelope.payload, settings)
    elif msg_type == MessageType.JOB_LEASE_RENEW:
        response_payload = await handle_job_lease_renew(db, node, envelope.payload, settings)
    elif msg_type == MessageType.JOB_EVENT:
        response_payload = await handle_job_event(db, node, envelope.payload, settings)
    elif msg_type == MessageType.JOB_CANCEL:
        response_payload = await handle_job_cancel(db, node, envelope.payload, settings)
    elif msg_type == MessageType.NODE_RECONCILE_JOBS:
        response_payload = await handle_reconcile_jobs(db, node, envelope.payload, settings)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message=f"Unsupported message_type: {msg_type}",
            ).model_dump(),
        )
    log_stage("handler_complete", envelope=envelope, node_id=authenticated_node_id)

    # 7. Return response envelope
    response: dict[str, object] = {
        "yqp_version": "0.1",
        "message_id": envelope.message_id,
        "message_type": _response_type(msg_type, response_payload),
        "trace_id": envelope.trace_id,
        "node_id": authenticated_node_id,
        "timestamp": now.isoformat(),
        "payload": response_payload,
    }
    log_stage("response_ready", envelope=envelope, node_id=authenticated_node_id)
    return response
