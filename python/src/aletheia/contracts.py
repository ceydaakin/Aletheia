"""Wire contracts shared by every Aletheia service.

This is the Python half of a contract whose Go half lives in
``gateway/internal/contract``. The two are checked against a golden JSON fixture
in CI (see ``tests/test_contracts.py``) — if you change a field here, change it
there in the same commit.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Mode(StrEnum):
    """What to do with claims the verifier could not support."""

    STRICT = "strict"
    """Remove unsupported claims from the answer."""
    FLAGGED = "flagged"
    """Keep them, but mark them in the response."""
    PERMISSIVE = "permissive"
    """Return the answer as generated. The guarantee does not hold here."""


class Decision(StrEnum):
    ANSWER = "answer"
    ANSWER_WITH_FLAGS = "answer_with_flags"
    ABSTAIN = "abstain"


class AbstainReason(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_SOURCES = "conflicting_sources"
    OUT_OF_CORPUS = "out_of_corpus"
    STALE_CALIBRATION = "stale_calibration"


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class ClaimAction(StrEnum):
    KEPT = "kept"
    FLAGGED = "flagged"
    REMOVED = "removed"


class Base(BaseModel):
    """Reject unknown fields everywhere.

    A silently ignored field is how two services end up disagreeing about the
    contract while both look healthy.
    """

    model_config = ConfigDict(extra="forbid")


class Chunk(Base):
    """A retrieved passage, identified so that a citation survives an amendment."""

    chunk_id: str
    doc_id: str
    version: int = 1
    title: str = ""
    text: str
    score: float = 0.0


class DraftClaim(Base):
    """A claim as generated: it knows what it cites, but nothing has checked it."""

    text: str
    citations: list[str] = Field(default_factory=list)


class Claim(Base):
    """A claim after verification, and possibly after the action policy."""

    text: str
    citations: list[str] = Field(default_factory=list)
    support_score: float = 0.0
    status: ClaimStatus = ClaimStatus.UNSUPPORTED
    action: ClaimAction | None = None


# --- Retrieval ---


class RetrieveRequest(Base):
    tenant_id: str
    query: str
    as_of: str = ""
    k: int = 24


class RetrieveResponse(Base):
    chunks: list[Chunk] = Field(default_factory=list)
    k: int = 0
    reranked_to: int = 0
    latency_ms: int = 0


# --- Generation ---


class GenerateRequest(Base):
    tenant_id: str
    query: str
    chunks: list[Chunk] = Field(default_factory=list)


class GenerateResponse(Base):
    answer: str = ""
    claims: list[DraftClaim] = Field(default_factory=list)


# --- Verifier ---


class VerifyRequest(Base):
    tenant_id: str
    claims: list[DraftClaim] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)


class VerifyResponse(Base):
    claims: list[Claim] = Field(default_factory=list)


# --- Risk controller ---


class DecideRequest(Base):
    tenant_id: str
    risk_budget: float = 0.05
    mode: Mode = Mode.STRICT
    claims: list[Claim] = Field(default_factory=list)


class DecideResponse(Base):
    """The verdict, plus claims with actions applied.

    The gateway reassembles the answer text from the surviving claims; this
    service never returns rewritten prose, so there is exactly one place where
    the text the guarantee is about gets constructed (ADR-0004).
    """

    decision: Decision
    statistic: float = 0.0
    threshold: float = 0.0
    calibration_id: str = ""
    guarantee: str = ""
    abstain_reason: AbstainReason | None = None
    degraded: bool = False
    claims: list[Claim] = Field(default_factory=list)
