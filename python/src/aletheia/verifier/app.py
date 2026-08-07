"""Verifier service — per-claim evidence checking.

Scores each claim against the chunks it cites and nothing else. Two rules survive
every change of backend:

**A claim with no citation is unsupported, whatever it says.** Plausibility is not
evidence, and a model asked to judge a claim against an empty premise returns a
number regardless.

**A citation that was not retrieved scores zero.** A dangling citation is worse
than no citation, because it looks like evidence to anyone reading the response.

The backend is selected by ``VERIFIER_BACKEND``; see :mod:`aletheia.verifier.nli`
for what each one can and cannot tell apart.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aletheia.contracts import (
    Chunk,
    Claim,
    ClaimStatus,
    DraftClaim,
    VerifyRequest,
    VerifyResponse,
)
from aletheia.service import create_app
from aletheia.settings import get_settings
from aletheia.verifier.nli import get_scorer

log = logging.getLogger("verifier")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.scorer = get_scorer(settings)
    log.info(
        "verifier started",
        extra={"extra_fields": {"backend": app.state.scorer.name}},
    )
    yield


app = create_app("verifier", lifespan=lifespan)


def premise_for(claim: DraftClaim, chunks_by_id: dict[str, Chunk]) -> str:
    """The evidence a claim is entitled to be judged against.

    Only what it cites. Scoring against every retrieved chunk would let a claim
    borrow support from a passage it never pointed at, which makes the citation
    decorative and the guarantee unverifiable.
    """
    return "\n\n".join(
        chunks_by_id[cid].text for cid in claim.citations if cid in chunks_by_id
    )


@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest) -> VerifyResponse:
    settings = get_settings()
    chunks_by_id = {c.chunk_id: c for c in req.chunks}

    # Claims that cannot be scored are settled here rather than being sent to a
    # model that would invent a number for them.
    scorable: list[tuple[int, str, str]] = []
    scores: list[float] = [0.0] * len(req.claims)

    for index, claim in enumerate(req.claims):
        if not claim.citations:
            continue
        premise = premise_for(claim, chunks_by_id)
        if not premise:
            continue
        scorable.append((index, premise, claim.text))

    if scorable:
        computed = app.state.scorer.score([(p, h) for _, p, h in scorable])
        for (index, _, _), score in zip(scorable, computed, strict=True):
            scores[index] = float(score)

    return VerifyResponse(
        claims=[
            Claim(
                text=claim.text,
                citations=claim.citations,
                support_score=round(score, 4),
                status=(
                    ClaimStatus.SUPPORTED
                    if score >= settings.support_threshold
                    else ClaimStatus.UNSUPPORTED
                ),
            )
            for claim, score in zip(req.claims, scores, strict=True)
        ]
    )
