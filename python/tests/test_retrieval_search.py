"""Hybrid search against a real Postgres.

The lexical arm's behaviour lives in the text search configurations and the
tsquery builder, both of which are SQL. Testing them anywhere but against the
database would test a mock.

Requires Postgres. Set ALETHEIA_TEST_DATABASE_URL, or these skip.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.conftest import TEST_TENANT

from aletheia.ingestion.store import ingest_document
from aletheia.retrieval.search import (
    dense_search,
    lexical_search,
    tenant_languages,
)

pytestmark = pytest.mark.db

JAN = datetime(2024, 1, 1, tzinfo=UTC)
JUN = datetime(2024, 6, 1, tzinfo=UTC)

TR_NOTICE = (
    "Taraflardan her biri, otuz gün önceden yazılı ihbarda bulunmak suretiyle "
    "sözleşmeyi feshedebilir."
)
TR_RETENTION = (
    "Çalışan özlük dosyaları, iş ilişkisinin sona ermesinden itibaren on yıl "
    "süreyle saklanır."
)
EN_NOTICE = (
    "Either party may terminate this agreement by giving thirty days written notice."
)


async def seed(db, doc_id: str, text: str, *, lang: str, valid_from=JAN) -> None:
    async with db.transaction() as conn:
        await ingest_document(
            conn, tenant_id=TEST_TENANT, doc_id=doc_id, text=text,
            lang=lang, valid_from=valid_from,
        )


async def search(db, query: str, *, lang: str = "", k: int = 10, valid_at=None):
    async with db.connection() as conn:
        return await lexical_search(
            conn, TEST_TENANT, query,
            k=k, valid_at=valid_at, lang=lang,
        )


# --- Lexical arm -----------------------------------------------------------


async def test_turkish_morphology_is_stemmed(db) -> None:
    """The query says 'sözleşme', the corpus says 'sözleşmeyi'.

    Turkish is agglutinative: without stemming this is a miss, and the arm that
    exists to catch morphological variants catches nothing.
    """
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    results = await search(db, "sözleşme feshi", lang="tr")
    assert [c.doc_id for c in results] == ["notice.md"]


async def test_unaccented_query_matches_accented_corpus(db) -> None:
    """Turkish is routinely typed without diacritics even when the corpus has them."""
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    results = await search(db, "sozlesme fesih ihbar", lang="tr")
    assert [c.doc_id for c in results] == ["notice.md"]


async def test_a_long_question_does_not_require_every_word(db) -> None:
    """The defect that put recall at 0.02: conjunctive query parsing.

    Questions are longer than the passages that answer them, so ANDing the terms
    means the passage must contain the whole question.
    """
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    results = await search(
        db, "Sözleşmeyi feshetmek için kaç gün önceden ihbar gerekir?", lang="tr"
    )
    assert [c.doc_id for c in results] == ["notice.md"]


async def test_english_stemming(db) -> None:
    await seed(db, "notice-en.md", EN_NOTICE, lang="en")

    results = await search(db, "terminating the agreement", lang="en")
    assert [c.doc_id for c in results] == ["notice-en.md"]


async def test_ranking_prefers_the_denser_match(db) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr")
    await seed(db, "retention.md", TR_RETENTION, lang="tr")

    results = await search(db, "ihbar süresi fesih", lang="tr")
    assert results[0].doc_id == "notice.md"


async def test_a_query_with_no_shared_terms_returns_nothing(db) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    assert await search(db, "kuantum bilgisayar mimarisi", lang="tr") == []


async def test_a_stopword_only_query_returns_nothing(db) -> None:
    """query_tsquery yields NULL here; the arm must return nothing, not everything."""
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    assert await search(db, "ve ile bir bu", lang="tr") == []


async def test_empty_query_returns_nothing(db) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    assert await search(db, "", lang="tr") == []


async def test_punctuation_does_not_raise(db) -> None:
    """A syntax error inside a query parser would be an outage."""
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    for query in ["fesih & | ! ()", "'quoted", "a:b:c", "<->", "%_"]:
        await search(db, query, lang="tr")


async def test_k_bounds_the_result_set(db) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr")
    await seed(db, "retention.md", TR_RETENTION, lang="tr")

    assert len(await search(db, "sözleşme çalışan saklanır fesih", lang="tr", k=1)) == 1


# --- Filtering happens before ranking --------------------------------------


async def test_superseded_versions_are_not_retrieved(db) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr", valid_from=JAN)
    async with db.transaction() as conn:
        await ingest_document(
            conn, tenant_id=TEST_TENANT, doc_id="notice.md",
            text="Fesih ihbar süresi altmış güne çıkarılmıştır.",
            lang="tr", valid_from=JUN,
        )

    current = await search(db, "fesih ihbar", lang="tr")
    assert [c.version for c in current] == [2]
    assert "altmış" in current[0].text


async def test_as_of_selects_the_historical_version(db) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr", valid_from=JAN)
    async with db.transaction() as conn:
        await ingest_document(
            conn, tenant_id=TEST_TENANT, doc_id="notice.md",
            text="Fesih ihbar süresi altmış güne çıkarılmıştır.",
            lang="tr", valid_from=JUN,
        )

    historical = await search(
        db, "fesih ihbar", lang="tr", valid_at=JAN + timedelta(days=1)
    )
    assert [c.version for c in historical] == [1]
    assert "otuz" in historical[0].text


async def test_tenants_cannot_see_each_other(db) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO tenants (tenant_id, name, api_key_hash) VALUES ('other','Other','x')"
        )
        await ingest_document(
            conn, tenant_id="other", doc_id="notice.md", text=TR_NOTICE,
            lang="tr", valid_from=JAN,
        )

    assert await search(db, "fesih ihbar", lang="tr") == []


# --- Dense arm -------------------------------------------------------------


async def set_embedding(db, chunk_id_suffix: str, vector: list[float]) -> None:
    """Write a vector directly.

    The real embedder lives behind the `models` extra, and the point here is the
    SQL — that cosine ordering works, that the temporal predicates apply, and that
    unembedded chunks are excluded.
    """
    literal = "[" + ",".join(str(v) for v in vector) + "]"
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE chunks SET embedding = %s::vector WHERE tenant_id = %s AND chunk_id LIKE %s",
            (literal, TEST_TENANT, f"%{chunk_id_suffix}%"),
        )


def unit(index: int, dimension: int = 768) -> list[float]:
    vector = [0.0] * dimension
    vector[index] = 1.0
    return vector


async def test_dense_ranks_by_cosine_distance(db) -> None:
    await seed(db, "a.md", TR_NOTICE, lang="tr")
    await seed(db, "b.md", TR_RETENTION, lang="tr")
    await set_embedding(db, "a.md", unit(0))
    await set_embedding(db, "b.md", unit(1))

    async with db.connection() as conn:
        results = await dense_search(
            conn, TEST_TENANT, unit(0), k=10
        )

    assert [c.doc_id for c in results] == ["a.md", "b.md"]
    assert results[0].score == pytest.approx(1.0, abs=1e-6)


async def test_dense_excludes_unembedded_chunks(db) -> None:
    """Without the IS NOT NULL predicate these sort last rather than being excluded,
    padding the list with unranked chunks (ADR-0006)."""
    await seed(db, "a.md", TR_NOTICE, lang="tr")
    await seed(db, "b.md", TR_RETENTION, lang="tr")
    await set_embedding(db, "a.md", unit(0))

    async with db.connection() as conn:
        results = await dense_search(
            conn, TEST_TENANT, unit(0), k=10
        )

    assert [c.doc_id for c in results] == ["a.md"]


async def test_dense_respects_as_of(db) -> None:
    await seed(db, "notice.md", TR_NOTICE, lang="tr", valid_from=JUN)
    await set_embedding(db, "notice.md", unit(0))

    async with db.connection() as conn:
        before = await dense_search(
            conn, TEST_TENANT, unit(0), k=10, valid_at=JAN,
        )
    assert before == []


async def test_dense_with_no_embeddings_at_all_returns_nothing(db) -> None:
    """Degrading to lexical-only is what keeps the stack runnable without torch."""
    await seed(db, "notice.md", TR_NOTICE, lang="tr")

    async with db.connection() as conn:
        results = await dense_search(
            conn, TEST_TENANT, unit(0), k=10
        )
    assert results == []


# --- Language discovery ----------------------------------------------------


async def test_tenant_languages(db) -> None:
    await seed(db, "tr.md", TR_NOTICE, lang="tr")
    await seed(db, "en.md", EN_NOTICE, lang="en")

    async with db.connection() as conn:
        assert await tenant_languages(conn, TEST_TENANT) == ["en", "tr"]
