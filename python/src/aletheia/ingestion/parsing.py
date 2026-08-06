"""Document parsing: bytes in, plain text plus metadata out.

Text extraction is where the corpus gets its identity: ``content_sha256`` hashes
what comes out of here, so a change in this module versions every affected
document (ADR-0005). ``PARSER_VERSION`` is recorded on each document row so that a
mass reversion is explainable rather than mysterious — bump it when extraction
behaviour changes in a way that alters output.

**Known limitation.** PDF extraction is per-page text, not layout-aware. Tables in
SEC 10-K filings — one of the target corpora — will come out as run-together
cells. PRD F1 asks for layout-aware parsing; that is a week-3 upgrade evaluated
against Recall@10, not a claim this module currently makes.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

PARSER_VERSION = "1"

MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class UnsupportedMediaType(ValueError):
    """Raised for a format with no parser. A poison message, not a retryable error."""


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    title: str
    media_type: str
    parser: str
    parser_version: str = PARSER_VERSION


def detect_media_type(filename: str, declared: str = "") -> str:
    """Prefer the caller's declaration, fall back to the extension."""
    if declared:
        # Strip any charset parameter: "text/html; charset=utf-8".
        return declared.split(";", 1)[0].strip().lower()
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise UnsupportedMediaType(
            f"cannot infer a media type from {filename!r}; "
            f"supported extensions: {', '.join(sorted(MEDIA_TYPES))}"
        )
    return MEDIA_TYPES[suffix]


def parse(data: bytes, *, media_type: str, filename: str = "") -> ParsedDocument:
    parsers = {
        "text/plain": _parse_text,
        "text/markdown": _parse_markdown,
        "text/html": _parse_html,
        "application/pdf": _parse_pdf,
        MEDIA_TYPES[".docx"]: _parse_docx,
    }
    parser = parsers.get(media_type)
    if parser is None:
        raise UnsupportedMediaType(f"no parser for media type {media_type!r}")
    return parser(data, filename)


def _normalise(text: str) -> str:
    """Collapse the whitespace noise that would otherwise inflate every span.

    Runs of blank lines become exactly one, and trailing spaces go. Paragraph
    breaks are load-bearing for chunking, so they are preserved rather than
    flattened.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode(data: bytes) -> str:
    """UTF-8 with a pragmatic fallback.

    Turkish legislative texts still circulate in cp1254; decoding those as UTF-8
    with replacement characters would silently corrupt exactly the terminology the
    verifier has to match.
    """
    # utf-8-sig before utf-8: plain utf-8 decodes BOM'd bytes without error but
    # leaves the mark as a literal ﻿, which then becomes the first character
    # of the title and shifts every chunk span by one.
    for encoding in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _fallback_title(filename: str) -> str:
    return PurePosixPath(filename).stem.replace("_", " ").replace("-", " ").strip()


def _parse_text(data: bytes, filename: str) -> ParsedDocument:
    text = _normalise(_decode(data))
    first_line = text.split("\n", 1)[0].strip() if text else ""
    title = first_line if 0 < len(first_line) <= 120 else _fallback_title(filename)
    return ParsedDocument(text=text, title=title, media_type="text/plain", parser="text")


def _parse_markdown(data: bytes, filename: str) -> ParsedDocument:
    raw = _normalise(_decode(data))
    title = _fallback_title(filename)
    for line in raw.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break
    # Markdown is left as-is rather than rendered away: headings and list markers
    # are useful retrieval signal, and stripping them would shift every span.
    return ParsedDocument(text=raw, title=title, media_type="text/markdown", parser="markdown")


def _parse_html(data: bytes, filename: str) -> ParsedDocument:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_decode(data), "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif (h1 := soup.find("h1")) is not None:
        title = h1.get_text(strip=True)

    # Block-level separator so paragraphs survive as paragraphs; chunking splits
    # on those boundaries first.
    text = _normalise(soup.get_text(separator="\n\n"))
    return ParsedDocument(
        text=text,
        title=title or _fallback_title(filename),
        media_type="text/html",
        parser="html",
    )


def _parse_pdf(data: bytes, filename: str) -> ParsedDocument:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = _normalise("\n\n".join(pages))

    title = ""
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title).strip()
    return ParsedDocument(
        text=text,
        title=title or _fallback_title(filename),
        media_type="application/pdf",
        parser="pypdf",
    )


def _parse_docx(data: bytes, filename: str) -> ParsedDocument:
    import docx

    document = docx.Document(io.BytesIO(data))
    blocks = [p.text for p in document.paragraphs]
    # Tables are flattened to tab-separated rows. Crude, but dropping them
    # entirely would lose the numbers these corpora are mostly about.
    for table in document.tables:
        for row in table.rows:
            blocks.append("\t".join(cell.text.strip() for cell in row.cells))

    text = _normalise("\n\n".join(b for b in blocks if b.strip()))
    core = document.core_properties
    title = (core.title or "").strip() if core is not None else ""
    return ParsedDocument(
        text=text,
        title=title or _fallback_title(filename),
        media_type=MEDIA_TYPES[".docx"],
        parser="python-docx",
    )
