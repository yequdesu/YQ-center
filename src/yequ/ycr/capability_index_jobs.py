"""Background Tool RAG capability index jobs."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.ycr import YcrCapabilityIndex, YcrCapabilityIndexJob
from yequ.services.capability_registry import capability_search
from yequ.ycr.capability_gateway import (
    CAPABILITY_INDEX_VERSION,
    _capability_index_document,
    _capability_index_text,
    _index_id,
    _stable_hash,
    _vector_literal,
)
from yequ.ycr.embedding import EmbeddingError
from yequ.ycr.scheduler import (
    PRIORITY_BACKGROUND_CAPABILITY_INDEX,
    scheduled_embed_text_full,
)


async def enqueue_capability_index_jobs(
    db: AsyncSession,
    *,
    capability_ids: set[str] | None = None,
) -> int:
    candidates = await capability_search(
        db,
        query=None,
        projection="schema",
        capability_type="function",
        include_inactive=False,
        limit=1000,
        max_limit=1000,
    )
    count = 0
    for candidate in candidates:
        capability_id = str(candidate.get("capability_id") or "")
        if capability_ids and capability_id not in capability_ids:
            continue
        document = _capability_index_document(candidate)
        index_text = _capability_index_text(document)
        document_hash = _stable_hash(document)
        index_id = _index_id(candidate)
        existing = await db.execute(
            select(YcrCapabilityIndexJob).where(
                YcrCapabilityIndexJob.index_id == index_id,
                YcrCapabilityIndexJob.document_hash == document_hash,
                YcrCapabilityIndexJob.status.in_(["queued", "running", "succeeded"]),
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue
        job = YcrCapabilityIndexJob(
            job_id=_job_id(index_id=index_id, document_hash=document_hash),
            index_id=index_id,
            capability_id=capability_id,
            canonical_name=str(candidate.get("canonical_name") or ""),
            capability_type=str(candidate.get("capability_type") or "function"),
            document_hash=document_hash,
            document_json=document,
            index_text=index_text,
            status="queued",
        )
        db.add(job)
        count += 1
    return count


async def run_capability_index_jobs_once(db: AsyncSession, *, limit: int = 10) -> dict[str, int]:
    result = await db.execute(
        select(YcrCapabilityIndexJob)
        .where(YcrCapabilityIndexJob.status.in_(["queued", "failed"]))
        .order_by(YcrCapabilityIndexJob.updated_at.asc())
        .limit(max(1, min(limit, 100)))
    )
    jobs = list(result.scalars().all())
    succeeded = 0
    failed = 0
    for job in jobs:
        job.status = "running"
        job.attempt += 1
        await db.flush()
        try:
            embedding = await scheduled_embed_text_full(
                job.index_text,
                priority=PRIORITY_BACKGROUND_CAPABILITY_INDEX,
                purpose="capability.index.precompute",
                cache_key=job.document_hash,
            )
            record_result = await db.execute(
                select(YcrCapabilityIndex).where(YcrCapabilityIndex.index_id == job.index_id)
            )
            record = record_result.scalar_one_or_none()
            if record is None:
                record = YcrCapabilityIndex(index_id=job.index_id)
                db.add(record)
            record.capability_id = job.capability_id
            record.canonical_name = job.canonical_name
            record.capability_type = job.capability_type
            record.index_version = CAPABILITY_INDEX_VERSION
            record.document_hash = job.document_hash
            record.document_json = job.document_json
            record.index_text = job.index_text
            record.embedding_json = embedding.dense
            record.embedding_provider = embedding.provider
            record.embedding_model = embedding.model
            record.sparse_json = embedding.sparse
            await db.flush()
            if db.bind and db.bind.dialect.name == "postgresql":
                await db.execute(
                    text(
                        "UPDATE ycr_capability_index "
                        "SET embedding_vector = CAST(:embedding AS vector) "
                        "WHERE index_id = :index_id"
                    ),
                    {"embedding": _vector_literal(embedding.dense), "index_id": job.index_id},
                )
            job.status = "succeeded"
            job.last_error_code = None
            job.last_error_message = None
            succeeded += 1
        except EmbeddingError as exc:
            job.status = "failed"
            job.last_error_code = "capability_rag_unavailable"
            job.last_error_message = str(exc)[:1000]
            failed += 1
    return {"processed": len(jobs), "succeeded": succeeded, "failed": failed}


async def capability_index_status(db: AsyncSession) -> dict[str, object]:
    job_rows = await db.execute(
        select(YcrCapabilityIndexJob.status, func.count(YcrCapabilityIndexJob.job_id)).group_by(
            YcrCapabilityIndexJob.status
        )
    )
    counts = {str(status): int(count) for status, count in job_rows.all()}
    index_count = await db.scalar(select(func.count(YcrCapabilityIndex.index_id)))
    pending = counts.get("queued", 0) + counts.get("running", 0)
    return {
        "status": "ready" if pending == 0 else "indexing",
        "indexes": int(index_count or 0),
        "jobs": {
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
        },
    }


def _job_id(*, index_id: str, document_hash: str) -> str:
    seed = json.dumps({"index_id": index_id, "document_hash": document_hash}, sort_keys=True)
    return f"ycridx_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"
