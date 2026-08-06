"""Ingestion service: HTTP submission plus the JetStream consumer, one process.

Submission is asynchronous — ``POST /ingest`` writes a job row, publishes to NATS,
and returns 202 with a job id. Parsing a 300-page filing is not something to do
inside a request.

The service is degraded but useful without NATS: ``/readyz`` fails, so nothing
routes traffic to it, while ``/healthz`` stays green so the orchestrator does not
restart-loop a process whose only problem is that a dependency is late.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response

from aletheia.db import get_db
from aletheia.ingestion import jobs
from aletheia.ingestion.consumer import Consumer
from aletheia.ingestion.embedding import get_embedder
from aletheia.ingestion.messages import (
    DocumentList,
    DocumentSummary,
    IngestAccepted,
    IngestMessage,
    IngestRequest,
    JobStatus,
)
from aletheia.service import create_app
from aletheia.settings import get_settings

log = logging.getLogger("ingestion")

_consumer: Consumer | None = None


def _ready() -> bool:
    return _consumer is not None and _consumer.connected


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer
    settings = get_settings()
    db = get_db()
    await db.open()

    _consumer = Consumer(db, settings, get_embedder(settings))
    try:
        await _consumer.connect()
        await _consumer.start()
    except Exception:
        # Serve health checks and report not-ready rather than crash-looping.
        log.exception("could not start the ingest consumer; running degraded")

    yield

    if _consumer is not None:
        await _consumer.stop()
    await db.close()


app = create_app("ingestion", ready=_ready, lifespan=lifespan)


@app.post("/ingest", response_model=IngestAccepted, status_code=202)
async def submit(request: IngestRequest) -> IngestAccepted:
    if _consumer is None or not _consumer.connected:
        raise HTTPException(status_code=503, detail="ingest queue is unavailable")

    job_id = f"job_{uuid.uuid4().hex}"
    try:
        message = IngestMessage(job_id=job_id, **request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # The job row is written before publishing so that a status lookup right
    # after a 202 always finds something. The reverse order has a window where
    # the job appears not to exist.
    await jobs.create(get_db(), job_id=job_id, tenant_id=message.tenant_id, doc_id=message.doc_id)
    await _consumer.publish(message)
    return IngestAccepted(job_id=job_id)


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def job_status(job_id: str) -> JobStatus:
    async with get_db().connection() as conn:
        row = await jobs.get(conn, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such job")
    return JobStatus(**{k: v for k, v in row.items() if k in JobStatus.model_fields})


@app.get("/documents", response_model=DocumentList)
async def list_documents(tenant_id: str, response: Response) -> DocumentList:
    """Documents currently in force for a tenant, newest first."""
    async with get_db().connection() as conn:
        cur = await conn.execute(
            """
            SELECT doc_id, version, title, media_type, lang, chunk_count,
                   content_sha256, valid_from, parser, parser_version
            FROM current_documents
            WHERE tenant_id = %s
            ORDER BY valid_from DESC, doc_id
            """,
            (tenant_id,),
        )
        rows = await cur.fetchall()
    response.headers["Cache-Control"] = "no-store"
    return DocumentList(documents=[DocumentSummary(**row) for row in rows])
