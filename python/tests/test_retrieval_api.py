"""The retrieval service over HTTP, against a real corpus.

test_retrieval_search.py covers the search functions; this covers the wiring
around them — arm fan-out, fusion, reranking, and the as_of contract.

Requires Postgres. Set ALETHEIA_TEST_DATABASE_URL, or these skip.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from tests.conftest import TEST_TENANT

from aletheia.db import set_db
from aletheia.ingestion.store import ingest_document

pytestmark = pytest.mark.db

JAN = datetime(2024, 1, 1, tzinfo=UTC)
JUN = datetime(2024, 6, 1, tzinfo=UTC)

TR_NOTICE = (
    "Taraflardan her biri, otuz gün önceden yazılı ihbarda bulunmak suretiyle "
    "sözleşmeyi feshedebilir."
)
EN_RETENTION = (
    "Employee records are retained for ten years after the employment relationship ends."
)


@pytest.fixture
async def client(db):
    """A retrieval app wired to the test database.

    ``set_db`` overrides the process-wide handle before the app's lifespan reads
    it — the seam that lets the service talk to a database the test controls.

    ASGITransport rather than TestClient: TestClient drives the app from its own
    event loop in a separate thread, and a psycopg pool created on the test's loop
    cannot be used from another one.
    """
    from aletheia.retrieval.app import app

    set_db(db)
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                yield c
    finally:
        set_db(None)


async def seed(db, doc_id: str, text: str, *, lang: str, valid_from=JAN) -> None:
    async with db.transaction() as conn:
        await ingest_document(
            conn, tenant_id=TEST_TENANT, doc_id=doc_id, text=text,
            lang=lang, valid_from=valid_from,
        )


async def retrieve(client, **payload):
    body = {"tenant_id": TEST_TENANT, "query": "", "k": 10, **payload}
    response = await client.post("/retrieve", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def test_ready_with_a_database(db, client) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200


async def test_retrieves_and_reports_its_work(db, client) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    body = await retrieve(client, query="sözleşme feshi ihbar", lang="tr")

    assert [c["doc_id"] for c in body["chunks"]] == ["notice.md"]
    assert body["reranked_to"] == len(body["chunks"])
    assert body["latency_ms"] >= 0


async def test_empty_result_is_not_an_error(db, client) -> None:
    """An empty list is what the gateway turns into abstain(out_of_corpus).

    Returning 5xx instead would make a correct abstention look like a fault.
    """
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    body = await retrieve(client, query="kuantum bilgisayar mimarisi", lang="tr")
    assert body["chunks"] == []


async def test_top_n_bounds_what_generation_sees(db, client) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr")
    await seed(db, "retention.md", EN_RETENTION, lang="en")

    body = await retrieve(client, query="sözleşme retained records ihbar", top_n=1)
    assert len(body["chunks"]) == 1


async def test_language_is_inferred_from_the_corpus(db, client) -> None:
    """With no lang given, one lexical arm runs per language and RRF fuses them.

    A bilingual tenant would otherwise have one language stemmed with the wrong
    rules — the recall this arm exists to provide.
    """
    await seed(db, "notice.md", TR_NOTICE, lang="tr")
    await seed(db, "retention.md", EN_RETENTION, lang="en")

    turkish = await retrieve(client, query="sözleşmeyi feshetmek")
    english = await retrieve(client, query="retaining employee records")

    assert [c["doc_id"] for c in turkish["chunks"]] == ["notice.md"]
    assert [c["doc_id"] for c in english["chunks"]] == ["retention.md"]


async def test_as_of_selects_the_historical_version(db, client) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr", valid_from=JAN)
    async with db.transaction() as conn:
        await ingest_document(
            conn, tenant_id=TEST_TENANT, doc_id="notice.md",
            text="Fesih ihbar süresi altmış güne çıkarılmıştır.",
            lang="tr", valid_from=JUN,
        )

    current = await retrieve(client, query="fesih ihbar", lang="tr")
    historical = await retrieve(client, query="fesih ihbar", lang="tr", as_of="2024-03-01")

    assert current["chunks"][0]["version"] == 2
    assert historical["chunks"][0]["version"] == 1
    assert "otuz" in historical["chunks"][0]["text"]


async def test_malformed_as_of_is_refused_not_silently_ignored(db, client) -> None:
    """The caller asked about a specific point in history; answering about a
    different one is worse than refusing."""
    response = await client.post(
        "/retrieve",
        json={"tenant_id": TEST_TENANT, "query": "x", "as_of": "last tuesday"},
    )
    assert response.status_code == 422


async def test_tenants_are_isolated(db, client) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO tenants (tenant_id, name, api_key_hash) VALUES ('other','Other','x')"
        )
        await ingest_document(
            conn, tenant_id="other", doc_id="notice.md", text=TR_NOTICE,
            lang="tr", valid_from=JAN,
        )

    body = await retrieve(client, query="fesih ihbar", lang="tr")
    assert body["chunks"] == []
