"""The ingestion wire contract: HTTP request bodies and the NATS payload.

Documents arrive one of two ways, and both end up as the same message:

* ``content_b64`` — inline, for anything small. NATS caps payloads at 1 MiB by
  default, so this is not the path for a 300-page filing.
* ``source_uri`` — a ``file://`` path on a volume both the submitter and the
  worker can see. The realistic path for a corpus load.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime

from pydantic import Field, field_validator, model_validator

from aletheia.contracts import Base

# NATS's default max_payload is 1 MiB; leave room for the JSON envelope.
MAX_INLINE_BYTES = 768 * 1024


class IngestMessage(Base):
    """One document submission, as published to NATS."""

    job_id: str
    tenant_id: str
    doc_id: str
    filename: str = ""
    media_type: str = ""
    lang: str = ""
    title: str = ""

    content_b64: str = ""
    source_uri: str = ""

    # When this text took effect in the world. Defaults to ingestion time.
    valid_from: datetime | None = None
    # True only if the previous version was never correct. See ADR-0005: an
    # amendment recorded as a correction erases the period the old text held.
    correction: bool = False

    @model_validator(mode="after")
    def exactly_one_source(self) -> IngestMessage:
        if bool(self.content_b64) == bool(self.source_uri):
            raise ValueError("provide exactly one of content_b64 or source_uri")
        return self

    @field_validator("content_b64")
    @classmethod
    def decodable(cls, value: str) -> str:
        if not value:
            return value
        if len(value) > MAX_INLINE_BYTES * 4 // 3 + 8:
            raise ValueError(
                f"inline content exceeds {MAX_INLINE_BYTES} bytes; use source_uri instead"
            )
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"content_b64 is not valid base64: {exc}") from exc
        return value

    def content(self) -> bytes | None:
        return base64.b64decode(self.content_b64) if self.content_b64 else None


class IngestRequest(Base):
    """POST /ingest body. Same shape as the message, minus the server-assigned id."""

    tenant_id: str
    doc_id: str
    filename: str = ""
    media_type: str = ""
    lang: str = ""
    title: str = ""
    content_b64: str = ""
    source_uri: str = ""
    valid_from: datetime | None = None
    correction: bool = False


class IngestAccepted(Base):
    job_id: str
    status: str = "queued"


class JobStatus(Base):
    job_id: str
    tenant_id: str
    doc_id: str
    status: str
    outcome: str | None = None
    version: int | None = None
    chunk_count: int = 0
    attempts: int = 0
    error: str = ""
    created_at: datetime
    updated_at: datetime


class DocumentSummary(Base):
    doc_id: str
    version: int
    title: str
    media_type: str
    lang: str
    chunk_count: int
    content_sha256: str
    valid_from: datetime
    parser: str = ""
    parser_version: str = ""


class DocumentList(Base):
    documents: list[DocumentSummary] = Field(default_factory=list)
