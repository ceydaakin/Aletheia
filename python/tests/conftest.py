from __future__ import annotations

import os

import pytest

from aletheia.db import Database, configure_event_loop

# Must run before pytest-asyncio creates a loop, so it lives at import time
# rather than in a fixture.
configure_event_loop()

TEST_TENANT = "test"

# Order matters: children before parents.
_TABLES = (
    "request_traces",
    "calibration_examples",
    "calibrations",
    "corpus_events",
    "ingest_jobs",
    "chunks",
    "documents",
    "tenants",
)


@pytest.fixture(scope="session")
def database_url() -> str:
    """Where the db-marked tests run.

    Point this at a scratch database, not at the one you loaded a corpus into:
    the fixture truncates every table, including ``tenants``. Sharing the dev
    database works but costs you the seed rows and whatever you ingested.
    """
    url = os.environ.get("ALETHEIA_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip(
            "ALETHEIA_TEST_DATABASE_URL is not set. Start Postgres with "
            "`docker compose up -d postgres`, create a scratch database, and set "
            "the variable to e.g. postgresql://aletheia:aletheia@localhost:5432/aletheia_test"
        )
    return url


@pytest.fixture
async def db(database_url: str):
    """A clean database per test.

    Truncating rather than rolling back a transaction is deliberate: ingestion's
    correctness depends on constraints and on now(), and both behave differently
    inside a rolled-back outer transaction than they do in production.
    """
    database = Database(database_url, max_size=4)
    await database.open()
    async with database.transaction() as conn:
        await conn.execute(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE")
        await conn.execute(
            """
            INSERT INTO tenants (tenant_id, name, default_risk_budget, api_key_hash)
            VALUES (%s, 'Test tenant', 0.05, 'test')
            """,
            (TEST_TENANT,),
        )
    try:
        yield database
    finally:
        await database.close()
