"""Job bookkeeping for asynchronous ingestion."""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from aletheia.db import Database
from aletheia.ingestion.store import IngestResult


async def create(db: Database, *, job_id: str, tenant_id: str, doc_id: str) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO ingest_jobs (job_id, tenant_id, doc_id, status)
            VALUES (%s, %s, %s, 'queued')
            """,
            (job_id, tenant_id, doc_id),
        )


async def mark_running(db: Database, job_id: str) -> int:
    """Move to running and return the attempt count including this one.

    The counter lives in the database rather than in memory because the point of
    it is to survive the worker restarting — which is precisely what a poison
    message tends to cause.
    """
    async with db.transaction() as conn:
        cur = await conn.execute(
            """
            UPDATE ingest_jobs
            SET status = 'running', attempts = attempts + 1, updated_at = now()
            WHERE job_id = %s
            RETURNING attempts
            """,
            (job_id,),
        )
        row = await cur.fetchone()
        return int(row["attempts"]) if row else 1


async def mark_succeeded(db: Database, job_id: str, result: IngestResult) -> None:
    status = "unchanged" if not result.changed else "succeeded"
    async with db.transaction() as conn:
        await conn.execute(
            """
            UPDATE ingest_jobs
            SET status = %s, outcome = %s, version = %s, chunk_count = %s,
                error = '', updated_at = now()
            WHERE job_id = %s
            """,
            (status, str(result.outcome), result.version, result.chunk_count, job_id),
        )


async def mark_failed(db: Database, job_id: str, error: str) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE ingest_jobs SET status = 'failed', error = %s, updated_at = now() WHERE job_id = %s",
            # Truncated: a full traceback in a status field helps nobody, and the
            # log has the whole thing.
            (error[:2000], job_id),
        )


async def get(conn: AsyncConnection, job_id: str) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT * FROM ingest_jobs WHERE job_id = %s", (job_id,))
    return await cur.fetchone()
