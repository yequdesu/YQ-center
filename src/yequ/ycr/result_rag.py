"""Result RAG boundary for tool observations and context refs."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.ycr.ref_store import expand_ref, inspect_ref, schema_ref, search_ref, tail_ref


async def retrieve_result_context(
    db: AsyncSession,
    *,
    ref_id: str,
    query: str,
    limit: int = 10,
) -> dict[str, object]:
    return await search_ref(db, ref_id, query=query, limit=limit)


async def inspect_result_context(db: AsyncSession, *, ref_id: str) -> dict[str, object]:
    return await inspect_ref(db, ref_id)


async def expand_result_context(
    db: AsyncSession,
    *,
    ref_id: str,
    path: str = "$",
    limit: int = 20,
) -> dict[str, object]:
    return await expand_ref(db, ref_id, path=path, limit=limit)


async def tail_result_context(
    db: AsyncSession,
    *,
    ref_id: str,
    path: str = "$",
    lines: int = 40,
) -> dict[str, object]:
    return await tail_ref(db, ref_id, path=path, lines=lines)


async def schema_result_context(
    db: AsyncSession,
    *,
    ref_id: str,
    path: str = "$",
) -> dict[str, object]:
    return await schema_ref(db, ref_id, path=path)
