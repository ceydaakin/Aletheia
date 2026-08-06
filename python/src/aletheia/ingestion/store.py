"""Bitemporal writes — the implementation of ADR-0005.

Three outcomes, and telling them apart is the whole job:

``unchanged``  the extracted text hashes to what the current version already holds
``amended``    the world changed; the old version's valid interval closes
``corrected``  we were wrong; the old rows are superseded, keeping their interval

Everything here runs in one transaction per document version. A partially ingested
document is a corpus state no calibration was fitted on.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from psycopg import AsyncConnection

from aletheia.db import latest_version
from aletheia.embedding import Embedder, NullEmbedder
from aletheia.ingestion.chunking import ChunkConfig, chunk_text

log = logging.getLogger("ingestion.store")

# "Still in force" is whatever the SQL forever() function returns — one definition,
# in the schema, referenced by every predicate here. Postgres 'infinity' would be
# the semantically obvious choice but psycopg raises DataError reading it back, so
# forever() is a far-future timestamp that round-trips as datetime.max. New
# versions never pass it explicitly; the column default applies.


class Outcome(StrEnum):
    CREATED = "created"
    AMENDED = "amended"
    CORRECTED = "corrected"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class IngestResult:
    outcome: Outcome
    doc_id: str
    version: int
    chunk_count: int
    content_sha256: str

    @property
    def changed(self) -> bool:
        return self.outcome is not Outcome.UNCHANGED


class IngestError(Exception):
    """A request that cannot succeed however many times it is retried."""


def content_hash(text: str) -> str:
    """Identity of a document version.

    Hashes extracted text, not the uploaded bytes: a PDF re-exported with a new
    creation timestamp is not a corpus change, and treating it as one would
    manufacture drift on every nightly sync (ADR-0005).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_id(doc_id: str, version: int, ordinal: int) -> str:
    """Deterministic, so re-running ingestion reproduces every citation exactly."""
    return f"{doc_id}:v{version}:chunk_{ordinal:04d}"


async def ingest_document(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    doc_id: str,
    text: str,
    title: str = "",
    source_uri: str = "",
    media_type: str = "",
    lang: str = "",
    parser: str = "",
    parser_version: str = "",
    valid_from: datetime | None = None,
    correction: bool = False,
    embedder: Embedder | None = None,
    chunk_config: ChunkConfig | None = None,
) -> IngestResult:
    """Write one document version. Call inside a transaction.

    Args:
        valid_from: when this text took effect in the world. Defaults to now.
            Ignored for corrections, which inherit the interval they are fixing.
        correction: True if the previous version was never true. See ADR-0005 —
            getting this wrong erases the period during which the old text held.
            Only the version currently in force can be corrected; fixing a
            historical version is a real need but out of scope for v1.
    """
    if not text.strip():
        raise IngestError(f"{doc_id}: extracted text is empty; nothing to ingest")

    embedder = embedder or NullEmbedder()
    valid_from = valid_from or datetime.now(UTC)
    sha = content_hash(text)

    current = await _current_version(conn, tenant_id, doc_id)

    if current is not None and current["content_sha256"] == sha and not correction:
        # Byte-identical text: not a corpus change, so no event and no drift.
        return IngestResult(
            outcome=Outcome.UNCHANGED,
            doc_id=doc_id,
            version=int(current["version"]),
            chunk_count=int(current["chunk_count"]),
            content_sha256=sha,
        )

    if correction and current is None:
        raise IngestError(f"{doc_id}: nothing to correct; the document has no current version")

    # Versions never repeat, including over rows a correction retired: a chunk id
    # must resolve to exactly one row forever.
    version = await latest_version(conn, tenant_id, doc_id) + 1

    # Only the version currently in force can be written against, so the new row's
    # valid_to is always infinity and is left to the column default.
    if current is None:
        outcome = Outcome.CREATED
    elif correction:
        outcome = Outcome.CORRECTED
        # Inherit the interval being fixed: the correction asserts what should
        # have been recorded for that same period.
        valid_from = current["valid_from"]
        await _supersede(conn, tenant_id, doc_id, int(current["version"]))
    else:
        outcome = Outcome.AMENDED
        if valid_from <= current["valid_from"]:
            # This would produce an interval the exclusion constraint rejects, but
            # the constraint's error message is about GiST operators, not about
            # what the caller actually did wrong.
            raise IngestError(
                f"{doc_id}: valid_from {valid_from.isoformat()} is not after the current "
                f"version's {current['valid_from'].isoformat()}; an amendment must take "
                "effect later than what it amends (use correction=True to fix a mistake)"
            )
        await _close_interval(conn, tenant_id, doc_id, int(current["version"]), valid_from)

    chunks = chunk_text(text, chunk_config)
    if not chunks:
        raise IngestError(f"{doc_id}: text produced no chunks")

    vectors = embedder.embed([c.text for c in chunks])

    await conn.execute(
        """
        INSERT INTO documents (
            doc_id, tenant_id, version, title, source_uri, media_type, lang,
            content_sha256, parser, parser_version, chunk_count, valid_from
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            doc_id, tenant_id, version, title, source_uri, media_type, lang,
            sha, parser, parser_version, len(chunks), valid_from,
        ),
    )

    rows = [
        (
            chunk_id(doc_id, version, chunk.ordinal),
            tenant_id, doc_id, version, chunk.ordinal,
            chunk.text, chunk.start, chunk.end, lang,
            valid_from,
            _to_vector(vector),
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO chunks (
                chunk_id, tenant_id, doc_id, version, ordinal,
                text, span_start, span_end, lang,
                valid_from, embedding
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )

    await conn.execute(
        """
        INSERT INTO corpus_events (tenant_id, doc_id, version, event, chunk_count)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (tenant_id, doc_id, version, _event_name(outcome), len(chunks)),
    )

    log.info(
        "document ingested",
        extra={
            "extra_fields": {
                "tenant_id": tenant_id,
                "doc_id": doc_id,
                "version": version,
                "outcome": str(outcome),
                "chunks": len(chunks),
                "embedded": vectors[0] is not None if vectors else False,
            }
        },
    )
    return IngestResult(
        outcome=outcome,
        doc_id=doc_id,
        version=version,
        chunk_count=len(chunks),
        content_sha256=sha,
    )


def _event_name(outcome: Outcome) -> str:
    return "created" if outcome is Outcome.CREATED else str(outcome)


def _to_vector(vector: list[float] | None) -> str | None:
    """pgvector's text input format. NULL means "not embedded yet"."""
    if vector is None:
        return None
    return "[" + ",".join(f"{v:.6g}" for v in vector) + "]"


async def _current_version(
    conn: AsyncConnection, tenant_id: str, doc_id: str
) -> dict | None:
    cur = await conn.execute(
        """
        SELECT version, content_sha256, chunk_count, valid_from
        FROM documents
        WHERE tenant_id = %s AND doc_id = %s
          AND valid_to = forever()
          AND superseded_at = forever()
        ORDER BY version DESC
        LIMIT 1
        """,
        (tenant_id, doc_id),
    )
    return await cur.fetchone()


async def _close_interval(
    conn: AsyncConnection, tenant_id: str, doc_id: str, version: int, at: datetime
) -> None:
    """End the old version's validity where the new one begins.

    Runs before the insert so the exclusion constraint never sees an overlap, and
    touches chunks as well as the document — a chunk that outlives its parent
    version is retrievable evidence for a text no longer in force.
    """
    params = (at, tenant_id, doc_id, version)
    await conn.execute(
        """
        UPDATE documents SET valid_to = %s
        WHERE tenant_id = %s AND doc_id = %s AND version = %s
          AND superseded_at = forever()
        """,
        params,
    )
    await conn.execute(
        """
        UPDATE chunks SET valid_to = %s
        WHERE tenant_id = %s AND doc_id = %s AND version = %s
          AND superseded_at = forever()
        """,
        params,
    )


async def _supersede(conn: AsyncConnection, tenant_id: str, doc_id: str, version: int) -> None:
    """Retract knowledge without deleting it.

    The rows stay, and a query with ``known_at`` in the past still reproduces the
    mistake — which is what makes this an audit trail rather than a changelog.
    """
    params = (tenant_id, doc_id, version)
    await conn.execute(
        """
        UPDATE documents SET superseded_at = now()
        WHERE tenant_id = %s AND doc_id = %s AND version = %s
          AND superseded_at = forever()
        """,
        params,
    )
    await conn.execute(
        """
        UPDATE chunks SET superseded_at = now()
        WHERE tenant_id = %s AND doc_id = %s AND version = %s
          AND superseded_at = forever()
        """,
        params,
    )


async def retire_document(
    conn: AsyncConnection, *, tenant_id: str, doc_id: str, at: datetime | None = None
) -> bool:
    """Mark a document as no longer in force from ``at`` onwards.

    Not erasure: the rows remain and ``as_of`` before ``at`` still returns them.
    Genuine erasure for a KVKK/GDPR request conflicts with an append-only audit
    trail and is an open problem, not a solved one (ADR-0005).
    """
    at = at or datetime.now(UTC)
    current = await _current_version(conn, tenant_id, doc_id)
    if current is None:
        return False
    await _close_interval(conn, tenant_id, doc_id, int(current["version"]), at)
    return True
