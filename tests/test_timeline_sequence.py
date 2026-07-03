from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.timeline import TimelineEvent
from yequ.timeline_events import (
    add_timeline_event,
    add_timeline_events,
)


async def test_sequence_allocator_seeds_from_existing_events(db_session: AsyncSession):
    existing = TimelineEvent(
        global_seq=7,
        event_type="seed.event",
        actor_type="system",
        actor_id="test",
    )
    db_session.add(existing)
    await db_session.commit()

    event = TimelineEvent(
        global_seq=0,
        event_type="next.event",
        actor_type="system",
        actor_id="test",
    )
    await add_timeline_event(db_session, event)
    await db_session.commit()

    assert event.global_seq == 8


async def test_batch_sequence_allocator_assigns_monotonic_values(
    db_session: AsyncSession,
):
    events = [
        TimelineEvent(
            global_seq=0,
            event_type=f"batch.event.{i}",
            actor_type="system",
            actor_id="test",
        )
        for i in range(3)
    ]

    await add_timeline_events(db_session, events)
    single = TimelineEvent(
        global_seq=0,
        event_type="single.event",
        actor_type="system",
        actor_id="test",
    )
    await add_timeline_event(db_session, single)
    await db_session.commit()

    values = [event.global_seq for event in events] + [single.global_seq]
    assert values == sorted(values)
    assert len(set(values)) == 4
    assert values[0] >= 1
