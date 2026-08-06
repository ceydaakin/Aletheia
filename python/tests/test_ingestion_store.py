"""Bitemporal store tests — the week 2 milestone.

These assert the semantics ADR-0005 promises: idempotent re-ingestion, amendments
that preserve history, corrections that retract it, and a database that refuses
overlapping versions rather than trusting the application to get intervals right.

Requires Postgres. Set ALETHEIA_TEST_DATABASE_URL, or these skip.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from tests.conftest import TEST_TENANT

from aletheia.db import chunks_as_of, current_document, latest_version
from aletheia.ingestion.store import (
    IngestError,
    Outcome,
    chunk_id,
    content_hash,
    ingest_document,
    retire_document,
)

pytestmark = pytest.mark.db

V1 = "The notice period is thirty days. It applies to contracts signed after 2024."
V2 = "The notice period is sixty days. It applies to contracts signed after 2026."

JAN = datetime(2024, 1, 1, tzinfo=UTC)
JUN = datetime(2024, 6, 1, tzinfo=UTC)
DEC = datetime(2024, 12, 1, tzinfo=UTC)


async def ingest(db, text: str, *, doc_id: str = "policy", **kwargs):
    async with db.transaction() as conn:
        return await ingest_document(
            conn, tenant_id=TEST_TENANT, doc_id=doc_id, text=text, **kwargs
        )


async def texts_at(db, when: datetime | None = None, tenant: str = TEST_TENANT) -> list[str]:
    async with db.connection() as conn:
        rows = await chunks_as_of(conn, tenant, when)
    return [row["text"] for row in sorted(rows, key=lambda r: (r["doc_id"], r["ordinal"]))]


async def count(db, table: str) -> int:
    async with db.connection() as conn:
        cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")
        row = await cur.fetchone()
    return int(row["n"])


# --- Creation --------------------------------------------------------------


async def test_first_ingest_creates_version_one(db) -> None:
    result = await ingest(db, V1, valid_from=JAN, title="Policy", lang="en")

    assert result.outcome is Outcome.CREATED
    assert result.version == 1
    assert result.chunk_count >= 1
    assert result.content_sha256 == content_hash(V1)

    async with db.connection() as conn:
        doc = await current_document(conn, TEST_TENANT, "policy")
    assert doc is not None
    assert doc["title"] == "Policy"
    assert doc["chunk_count"] == result.chunk_count

    assert await texts_at(db) == [V1]


async def test_chunk_ids_are_deterministic(db) -> None:
    """Reproducible citations are what make eval runs reproducible (PRD G6)."""
    await ingest(db, V1, valid_from=JAN)

    async with db.connection() as conn:
        cur = await conn.execute(
            "SELECT chunk_id, ordinal FROM chunks WHERE doc_id = 'policy' ORDER BY ordinal"
        )
        rows = await cur.fetchall()

    for row in rows:
        assert row["chunk_id"] == chunk_id("policy", 1, row["ordinal"])


async def test_empty_text_is_rejected(db) -> None:
    with pytest.raises(IngestError):
        await ingest(db, "   \n\n  ")


# --- Idempotence -----------------------------------------------------------


async def test_reingesting_identical_text_is_a_no_op(db) -> None:
    """A nightly sync must not manufacture drift (ADR-0005)."""
    await ingest(db, V1, valid_from=JAN)
    events_before = await count(db, "corpus_events")

    result = await ingest(db, V1, valid_from=JUN)

    assert result.outcome is Outcome.UNCHANGED
    assert result.version == 1
    assert await count(db, "documents") == 1
    assert await count(db, "corpus_events") == events_before, (
        "an unchanged document emitted a corpus event and would trip drift detection"
    )


# --- Amendment -------------------------------------------------------------


async def test_amendment_preserves_the_previous_text_in_its_own_period(db) -> None:
    await ingest(db, V1, valid_from=JAN)
    result = await ingest(db, V2, valid_from=JUN)

    assert result.outcome is Outcome.AMENDED
    assert result.version == 2

    # The whole point of bitemporality: an as_of query before the amendment must
    # still return what was in force then.
    assert await texts_at(db, JAN + timedelta(days=1)) == [V1]
    assert await texts_at(db, DEC) == [V2]
    assert await texts_at(db) == [V2]


async def test_amendment_closes_the_old_interval_exactly_at_the_new_one(db) -> None:
    """No gap and no overlap: an instant belongs to exactly one version."""
    await ingest(db, V1, valid_from=JAN)
    await ingest(db, V2, valid_from=JUN)

    async with db.connection() as conn:
        cur = await conn.execute(
            "SELECT version, valid_from, valid_to FROM documents WHERE doc_id='policy' ORDER BY version"
        )
        rows = await cur.fetchall()

    assert rows[0]["valid_to"] == rows[1]["valid_from"] == JUN
    assert await texts_at(db, JUN) == [V2]
    assert await texts_at(db, JUN - timedelta(microseconds=1)) == [V1]


async def test_backdated_amendment_is_refused_with_a_useful_message(db) -> None:
    await ingest(db, V1, valid_from=JUN)

    with pytest.raises(IngestError, match="correction=True"):
        await ingest(db, V2, valid_from=JAN)


async def test_amendment_at_the_same_instant_is_refused(db) -> None:
    await ingest(db, V1, valid_from=JUN)
    with pytest.raises(IngestError):
        await ingest(db, V2, valid_from=JUN)


# --- Correction ------------------------------------------------------------


async def test_correction_retracts_rather_than_amends(db) -> None:
    await ingest(db, V1, valid_from=JAN)
    result = await ingest(db, V2, correction=True)

    assert result.outcome is Outcome.CORRECTED
    assert result.version == 2

    # The corrected text now holds for the *original* period: the old text was
    # never true, so there is no window in which it applied.
    assert await texts_at(db, JAN + timedelta(days=1)) == [V2]
    assert await texts_at(db) == [V2]


async def test_correction_keeps_the_mistake_visible_to_an_auditor(db) -> None:
    """`known_at` in the past must still reproduce what we used to assert.

    That is the difference between an audit trail and a changelog.
    """
    await ingest(db, V1, valid_from=JAN)
    async with db.connection() as conn:
        cur = await conn.execute("SELECT now() AS t")
        before_fix = (await cur.fetchone())["t"]

    await ingest(db, V2, correction=True)

    async with db.connection() as conn:
        rows = await chunks_as_of(conn, TEST_TENANT, JUN, before_fix)
    assert [r["text"] for r in rows] == [V1]

    # And nothing was deleted.
    assert await count(db, "documents") == 2


async def test_correction_without_an_existing_document_is_refused(db) -> None:
    with pytest.raises(IngestError, match="nothing to correct"):
        await ingest(db, V2, correction=True)


async def test_versions_never_repeat_across_corrections(db) -> None:
    """A chunk id must resolve to exactly one row forever."""
    await ingest(db, V1, valid_from=JAN)
    await ingest(db, V2, correction=True)
    await ingest(db, V1 + " Extra clause.", correction=True)

    async with db.connection() as conn:
        assert await latest_version(conn, TEST_TENANT, "policy") == 3
        cur = await conn.execute(
            "SELECT count(DISTINCT chunk_id) AS n, count(*) AS total FROM chunks WHERE tenant_id = %s",
            (TEST_TENANT,),
        )
        row = await cur.fetchone()
    assert row["n"] == row["total"], "chunk ids collided across versions"


# --- Invariants the database enforces --------------------------------------

async def test_overlapping_versions_are_rejected_by_the_database(db) -> None:
    """The exclusion constraint, not application code, is the last line of defence.

    Written directly against SQL because the point is that a bug in the Python
    interval arithmetic would still be caught.
    """
    await ingest(db, V1, valid_from=JAN)

    with pytest.raises(psycopg.errors.ExclusionViolation):
        async with db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO documents (doc_id, tenant_id, version, content_sha256, valid_from)
                VALUES ('policy', %s, 99, 'deadbeef', %s)
                """,
                (TEST_TENANT, JUN),
            )


async def test_a_failed_ingest_leaves_nothing_behind(db) -> None:
    """One document version, one transaction: no half-written corpus states."""
    await ingest(db, V1, valid_from=JAN)

    with pytest.raises(IngestError):
        await ingest(db, V2, valid_from=JAN - timedelta(days=1))

    assert await count(db, "documents") == 1
    assert await texts_at(db) == [V1]

    async with db.connection() as conn:
        cur = await conn.execute("SELECT valid_to FROM documents WHERE version = 1")
        row = await cur.fetchone()
    # The old interval must not have been closed by the attempt that failed.
    assert row["valid_to"].year == 9999


# --- Retirement and isolation ----------------------------------------------


async def test_retiring_a_document_is_not_deletion(db) -> None:
    await ingest(db, V1, valid_from=JAN)

    async with db.transaction() as conn:
        assert await retire_document(conn, tenant_id=TEST_TENANT, doc_id="policy", at=JUN)

    assert await texts_at(db) == []
    assert await texts_at(db, JAN + timedelta(days=1)) == [V1]
    assert await count(db, "chunks") >= 1


async def test_retiring_an_unknown_document_reports_it(db) -> None:
    async with db.transaction() as conn:
        assert not await retire_document(conn, tenant_id=TEST_TENANT, doc_id="ghost")


async def test_tenants_are_isolated(db) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO tenants (tenant_id, name, api_key_hash) VALUES ('other', 'Other', 'x')"
        )

    await ingest(db, V1, valid_from=JAN)
    async with db.transaction() as conn:
        await ingest_document(
            conn, tenant_id="other", doc_id="policy", text=V2, valid_from=JAN
        )

    # Same doc_id, same instant, different tenants: neither may see the other.
    assert await texts_at(db) == [V1]
    assert await texts_at(db, tenant="other") == [V2]


# --- Events ----------------------------------------------------------------


async def test_every_change_records_a_corpus_event(db) -> None:
    """The drift signal's source (PRD §5.2)."""
    await ingest(db, V1, valid_from=JAN)
    await ingest(db, V2, valid_from=JUN)
    await ingest(db, V2, valid_from=DEC)  # unchanged
    await ingest(db, V1, correction=True)

    async with db.connection() as conn:
        cur = await conn.execute("SELECT event, version FROM corpus_events ORDER BY id")
        rows = await cur.fetchall()

    assert [(r["event"], r["version"]) for r in rows] == [
        ("created", 1),
        ("amended", 2),
        ("corrected", 3),
    ]
