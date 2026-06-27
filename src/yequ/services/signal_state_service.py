"""Signal state query and freshness helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.signal_state import SignalState
from yequ.models.timeline import TimelineEvent
from yequ.services.timeline_writer import add_timeline_event


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def compute_signal_freshness(
    state: SignalState,
    *,
    now: datetime | None = None,
) -> str:
    """Return the effective freshness for a SignalState."""
    current = now or datetime.now(UTC)
    expires_at = _as_aware_utc(state.expires_at)
    if expires_at is not None and expires_at <= current:
        return "stale"
    return "fresh"


async def refresh_signal_freshness(
    db: AsyncSession,
    *,
    node_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Persist stale freshness for expired signal states."""
    stale_signals = await mark_stale_signals(db, node_id=node_id, now=now)
    changed = len(stale_signals)

    current = now or datetime.now(UTC)
    stmt = select(SignalState).where(SignalState.expires_at.is_not(None))
    if node_id:
        stmt = stmt.where(SignalState.node_id == node_id)

    result = await db.execute(stmt)
    changed = 0
    for state in result.scalars().all():
        effective = compute_signal_freshness(state, now=current)
        if effective == "stale":
            continue
        if state.freshness_status != effective:
            state.freshness_status = effective
            changed += 1

    if changed:
        await db.commit()
    return changed


async def _write_signal_timeline(
    db: AsyncSession,
    event_type: str,
    state: SignalState,
    *,
    data: dict[str, object] | None = None,
) -> None:
    event_data: dict[str, object] = {
        "node_id": state.node_id,
        "signal_name": state.signal_name,
        "freshness_status": state.freshness_status,
        "quality": state.quality,
    }
    if data:
        event_data.update(data)

    event = TimelineEvent(
        global_seq=0,
        event_type=event_type,
        actor_type="system",
        actor_id="signal_state",
        node_id=state.node_id,
        data=event_data,
        timestamp=datetime.now(UTC),
    )
    await add_timeline_event(db, event)


async def mark_stale_signals(
    db: AsyncSession,
    *,
    node_id: str | None = None,
    now: datetime | None = None,
    write_timeline: bool = True,
) -> list[SignalState]:
    """Persist expired signal states as stale and emit one audit event per transition."""
    current = now or datetime.now(UTC)
    stmt = select(SignalState).where(SignalState.expires_at.is_not(None))
    if node_id:
        stmt = stmt.where(SignalState.node_id == node_id)

    result = await db.execute(stmt)
    stale_signals: list[SignalState] = []
    for state in result.scalars().all():
        if compute_signal_freshness(state, now=current) != "stale":
            continue
        if state.freshness_status == "stale":
            continue

        previous_quality = state.quality
        state.freshness_status = "stale"
        if state.quality == "ok":
            state.quality = "stale"
        stale_signals.append(state)

        if write_timeline:
            await _write_signal_timeline(
                db,
                "signal.stale",
                state,
                data={
                    "reason": "ttl_expired",
                    "expires_at": state.expires_at.isoformat() if state.expires_at else None,
                    "reported_at": state.reported_at.isoformat(),
                    "previous_quality": previous_quality,
                },
            )

    if stale_signals:
        await db.commit()
    return stale_signals


async def list_signal_states(
    db: AsyncSession,
    *,
    node_id: str | None = None,
    signal_name: str | None = None,
    freshness_status: str | None = None,
    refresh: bool = True,
) -> list[SignalState]:
    """List current signal states, optionally filtering by node and freshness."""
    if refresh:
        await refresh_signal_freshness(db, node_id=node_id)

    stmt = select(SignalState)
    if node_id:
        stmt = stmt.where(SignalState.node_id == node_id)
    if signal_name:
        stmt = stmt.where(SignalState.signal_name == signal_name)
    if freshness_status:
        stmt = stmt.where(SignalState.freshness_status == freshness_status)
    stmt = stmt.order_by(SignalState.node_id, SignalState.signal_name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


def signal_state_to_dict(state: SignalState) -> dict[str, object]:
    """Serialize a SignalState for Admin API responses."""
    return {
        "node_id": state.node_id,
        "capability_id": state.capability_id,
        "signal_name": state.signal_name,
        "value": state.value,
        "value_schema": state.value_schema,
        "scope": state.scope,
        "ttl_sec": state.ttl_sec,
        "freshness_status": state.freshness_status,
        "quality": state.quality,
        "collected_at": state.collected_at.isoformat() if state.collected_at else None,
        "reported_at": state.reported_at.isoformat() if state.reported_at else None,
        "expires_at": state.expires_at.isoformat() if state.expires_at else None,
    }
