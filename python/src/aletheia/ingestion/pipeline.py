"""Fetch → parse → chunk → embed → store, for one document.

Split out from the worker so it can be driven directly — by tests, by the CLI, and
by the eval harness — without standing up NATS.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from psycopg import AsyncConnection

from aletheia.db import Database
from aletheia.ingestion.chunking import ChunkConfig
from aletheia.ingestion.embedding import Embedder
from aletheia.ingestion.messages import IngestMessage
from aletheia.ingestion.parsing import UnsupportedMediaType, detect_media_type, parse
from aletheia.ingestion.store import IngestError, IngestResult, ingest_document
from aletheia.settings import Settings

log = logging.getLogger("ingestion.pipeline")


def chunk_config(settings: Settings) -> ChunkConfig:
    return ChunkConfig(
        target_chars=settings.chunk_target_chars,
        overlap_chars=settings.chunk_overlap_chars,
        min_chars=settings.chunk_min_chars,
    )


def read_source(message: IngestMessage) -> tuple[bytes, str]:
    """Return the document bytes and a filename to infer the media type from."""
    inline = message.content()
    if inline is not None:
        return inline, message.filename

    parsed = urlparse(message.source_uri)
    if parsed.scheme not in ("file", ""):
        raise IngestError(
            f"unsupported source_uri scheme {parsed.scheme!r}; only file:// is supported. "
            "Fetching over the network is out of scope for v1 (PRD non-goals)."
        )

    # url2pathname rather than hand-rolled slicing: it handles percent-encoding
    # and the Windows drive-letter form (file:///C:/x.pdf) correctly, and both
    # appear in practice because the CLI runs on developer machines.
    path = Path(url2pathname(parsed.path))
    if not path.is_file():
        raise IngestError(f"source_uri does not point at a readable file: {message.source_uri}")
    return path.read_bytes(), message.filename or path.name


async def process(
    conn: AsyncConnection,
    message: IngestMessage,
    *,
    settings: Settings,
    embedder: Embedder,
) -> IngestResult:
    """Ingest one document inside the caller's transaction."""
    data, filename = read_source(message)
    try:
        media_type = detect_media_type(filename, message.media_type)
        parsed = parse(data, media_type=media_type, filename=filename)
    except UnsupportedMediaType as exc:
        # Retrying will not make the format supported.
        raise IngestError(str(exc)) from exc

    return await ingest_document(
        conn,
        tenant_id=message.tenant_id,
        doc_id=message.doc_id,
        text=parsed.text,
        title=message.title or parsed.title,
        source_uri=message.source_uri,
        media_type=parsed.media_type,
        lang=message.lang,
        parser=parsed.parser,
        parser_version=parsed.parser_version,
        valid_from=message.valid_from,
        correction=message.correction,
        embedder=embedder,
        chunk_config=chunk_config(settings),
    )


async def run(
    db: Database,
    message: IngestMessage,
    *,
    settings: Settings,
    embedder: Embedder,
) -> IngestResult:
    """Ingest one document in its own transaction.

    One document version, one transaction: a half-written document is a corpus
    state no calibration was fitted on (ADR-0005).
    """
    async with db.transaction() as conn:
        return await process(conn, message, settings=settings, embedder=embedder)
