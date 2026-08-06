"""Chunking tests.

The load-bearing property is span accuracy: a citation points at a character range
of a document version, so a chunk whose span does not reproduce its own text makes
every citation over it a lie. That is asserted on every input here, not sampled.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from aletheia.ingestion.chunking import ChunkConfig, TextChunk, chunk_text

LEGAL_TR = """Hizmet Sözleşmesi

Taraflardan her biri, otuz (30) gün önceden yazılı ihbarda bulunmak suretiyle işbu
sözleşmeyi feshedebilir. Fesih ihbarı, karşı tarafın tebligat adresine iadeli
taahhütlü posta ile gönderilir.

Bu bölümdeki ihbar koşulları, 1 Ocak 2024 tarihinden sonra imzalanan sözleşmelere
uygulanır. Daha önce imzalanmış sözleşmeler için önceki hükümler geçerliliğini
korur.

Uyuşmazlık hâlinde İstanbul mahkemeleri yetkilidir."""


def assert_spans_reconstruct(source: str, chunks: list[TextChunk]) -> None:
    for chunk in chunks:
        assert chunk.text == source[chunk.start : chunk.end], (
            f"chunk {chunk.ordinal} span [{chunk.start}, {chunk.end}) does not reproduce its text"
        )


def test_spans_reconstruct_the_source() -> None:
    chunks = chunk_text(LEGAL_TR)
    assert chunks
    assert_spans_reconstruct(LEGAL_TR, chunks)


def test_ordinals_are_dense_and_ordered() -> None:
    chunks = chunk_text(LEGAL_TR, ChunkConfig(target_chars=200, overlap_chars=40))
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    # Chunks advance through the document; overlap may repeat text but never
    # rewinds the start pointer.
    starts = [c.start for c in chunks]
    assert starts == sorted(starts)


def test_full_coverage_no_text_is_dropped() -> None:
    """Every non-whitespace character must appear in at least one chunk.

    A dropped paragraph is silently unanswerable — retrieval cannot find it and
    the system abstains for a reason that looks like a corpus gap.
    """
    chunks = chunk_text(LEGAL_TR, ChunkConfig(target_chars=180, overlap_chars=30, min_chars=0))
    covered = bytearray(len(LEGAL_TR))
    for chunk in chunks:
        for i in range(chunk.start, chunk.end):
            covered[i] = 1
    missed = [
        i for i, flag in enumerate(covered) if not flag and not LEGAL_TR[i].isspace()
    ]
    assert not missed, f"characters dropped at offsets {missed[:20]}"


def test_target_size_is_respected() -> None:
    config = ChunkConfig(target_chars=300, overlap_chars=50)
    for chunk in chunk_text(LEGAL_TR, config):
        assert len(chunk.text) <= config.target_chars


def test_overlap_carries_context_across_boundaries() -> None:
    chunks = chunk_text(LEGAL_TR, ChunkConfig(target_chars=200, overlap_chars=80))
    assert len(chunks) > 1
    overlaps = [earlier.end > later.start for earlier, later in pairwise(chunks)]
    assert any(overlaps), "no chunk overlaps its successor; boundary claims lose their context"


def test_zero_overlap_produces_a_partition() -> None:
    chunks = chunk_text(LEGAL_TR, ChunkConfig(target_chars=200, overlap_chars=0))
    for earlier, later in pairwise(chunks):
        assert earlier.end <= later.start


def test_oversized_paragraph_is_hard_split() -> None:
    """A paragraph with no internal boundary still has to be cut somewhere."""
    source = "A" * 5000
    chunks = chunk_text(source, ChunkConfig(target_chars=1000, overlap_chars=100))

    assert len(chunks) == 5
    assert_spans_reconstruct(source, chunks)
    assert "".join(c.text for c in chunks) == source


def test_short_tail_is_merged_backwards() -> None:
    source = "First sentence here, long enough to matter.\n\nArticle 7."
    chunks = chunk_text(source, ChunkConfig(target_chars=45, overlap_chars=0, min_chars=40))

    assert len(chunks) == 1
    assert chunks[0].text.endswith("Article 7.")


@pytest.mark.parametrize("source", ["", "   ", "\n\n\n", "\t \n"])
def test_empty_input_yields_no_chunks(source: str) -> None:
    assert chunk_text(source) == []


def test_single_short_document_is_one_chunk() -> None:
    source = "The notice period is thirty days."
    chunks = chunk_text(source)
    assert len(chunks) == 1
    assert chunks[0].text == source
    assert (chunks[0].start, chunks[0].end) == (0, len(source))


def test_chunking_is_deterministic() -> None:
    """Eval reproducibility (PRD G6) depends on this and on nothing else."""
    assert chunk_text(LEGAL_TR) == chunk_text(LEGAL_TR)


def test_config_rejects_incoherent_settings() -> None:
    with pytest.raises(ValueError):
        ChunkConfig(target_chars=0)
    with pytest.raises(ValueError):
        ChunkConfig(overlap_chars=-1)
    with pytest.raises(ValueError):
        # Overlap at or above the target cannot make progress.
        ChunkConfig(target_chars=100, overlap_chars=100)


def test_terminates_on_pathological_input() -> None:
    """Overlap plus tiny targets is where a greedy packer loops forever."""
    source = ". ".join(f"Sentence number {i}" for i in range(200))
    chunks = chunk_text(source, ChunkConfig(target_chars=60, overlap_chars=55, min_chars=0))
    assert chunks
    assert_spans_reconstruct(source, chunks)
