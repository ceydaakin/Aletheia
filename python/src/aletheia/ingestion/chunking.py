"""Span-preserving chunking.

The invariant every function here maintains: ``chunk.text == source[chunk.start:chunk.end]``.
Citations point at a character range of a specific document version, so a chunk
whose span does not reproduce its own text makes every citation over it a lie. The
tests assert exact reconstruction rather than approximate similarity for that
reason.

Splitting is boundary-aware in descending order of preference — paragraph, then
sentence, then hard cut — because a claim split across a chunk boundary cannot be
entailed by either half, and an unverifiable claim costs answer rate (PRD G2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Blank line, optionally with surrounding whitespace.
_PARAGRAPH = re.compile(r"\n\s*\n")
# Sentence end: terminal punctuation, closing quotes/brackets, then whitespace.
# Deliberately naive about abbreviations — over-splitting costs a little context,
# under-splitting produces chunks that blow the target size.
_SENTENCE = re.compile(r'(?<=[.!?…])["\')\]]*\s+')


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    text: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid span [{self.start}, {self.end})")


@dataclass(frozen=True)
class ChunkConfig:
    target_chars: int = 1200
    """Roughly 250–300 tokens for Latin script, fewer for agglutinative Turkish."""
    overlap_chars: int = 150
    """Carried from the end of the previous chunk, so a claim near a boundary still
    has its context in at least one chunk."""
    min_chars: int = 120
    """Below this a chunk carries no retrievable signal; it is merged backwards."""

    def __post_init__(self) -> None:
        if self.target_chars <= 0:
            raise ValueError("target_chars must be positive")
        if self.overlap_chars < 0:
            raise ValueError("overlap_chars must not be negative")
        if self.overlap_chars >= self.target_chars:
            raise ValueError("overlap_chars must be smaller than target_chars")
        if self.min_chars < 0:
            raise ValueError("min_chars must not be negative")


def _segments(text: str) -> list[tuple[int, int]]:
    """Split into the smallest units chunking is allowed to break between."""
    spans: list[tuple[int, int]] = []
    for para_start, para_end in _split_spans(text, 0, len(text), _PARAGRAPH):
        if para_end - para_start == 0:
            continue
        spans.extend(_split_spans(text, para_start, para_end, _SENTENCE))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def _split_spans(
    text: str, start: int, end: int, pattern: re.Pattern[str]
) -> list[tuple[int, int]]:
    """Split ``text[start:end]`` on ``pattern``, returning absolute spans.

    Separators are dropped from the spans but the offsets stay absolute, so a
    chunk assembled from adjacent segments still slices back to itself.
    """
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in pattern.finditer(text, start, end):
        if match.start() > cursor:
            spans.append((cursor, match.start()))
        cursor = match.end()
    if cursor < end:
        spans.append((cursor, end))
    return spans


def chunk_text(text: str, config: ChunkConfig | None = None) -> list[TextChunk]:
    """Split ``text`` into overlapping, span-accurate chunks."""
    config = config or ChunkConfig()
    if not text.strip():
        return []

    segments = _segments(text)
    if not segments:
        return []

    # Greedily pack segments up to the target, then step back by the overlap.
    packed: list[tuple[int, int]] = []
    index = 0
    while index < len(segments):
        first = index
        start, end = segments[index]
        index += 1
        while index < len(segments) and segments[index][1] - start <= config.target_chars:
            end = segments[index][1]
            index += 1

        # A single segment longer than the target has to be cut mid-sentence.
        # No overlap step-back here: the segment is already fully consumed, and
        # rewinding into a hard-split run would not land on a real boundary.
        if end - start > config.target_chars:
            packed.extend(_hard_split(start, end, config.target_chars))
            continue

        packed.append((start, end))

        if index < len(segments) and config.overlap_chars:
            index = _step_back(segments, first, index, end - config.overlap_chars)

    merged = _merge_short_tail(packed, config.min_chars)
    return [
        TextChunk(ordinal=i, text=text[s:e], start=s, end=e)
        for i, (s, e) in enumerate(merged)
    ]


def _hard_split(start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Last resort for a segment with no internal boundary — a long table row, a
    URL list, a paragraph without terminal punctuation."""
    return [(s, min(s + target, end)) for s in range(start, end, target)]


def _step_back(segments: list[tuple[int, int]], first: int, index: int, floor: int) -> int:
    """Rewind so the next chunk re-includes trailing context.

    Takes as many trailing segments as fit in the overlap window, but always at
    least one when the chunk spanned several — legal prose routinely has single
    sentences longer than the overlap budget, and a purely size-driven rule would
    then produce no overlap at all, which is the case this exists to prevent.

    Stops strictly after ``first``, the segment this chunk began at. Rewinding to
    ``first`` itself would re-emit the same chunk forever, so that bound is what
    makes the caller's loop terminate.
    """
    candidate = index
    while candidate - 1 > first and segments[candidate - 1][0] >= floor:
        candidate -= 1
    if candidate == index and index - 1 > first:
        candidate = index - 1
    return candidate


def _merge_short_tail(spans: list[tuple[int, int]], min_chars: int) -> list[tuple[int, int]]:
    """Fold a runt final chunk into its predecessor.

    A 12-character trailing chunk ("Article 7.") retrieves noisily and entails
    nothing; it belongs with the text it came from.
    """
    if len(spans) < 2:
        return spans
    last_start, last_end = spans[-1]
    if last_end - last_start >= min_chars:
        return spans
    prev_start, prev_end = spans[-2]
    return [*spans[:-2], (prev_start, max(prev_end, last_end))]
