"""Verifier service — per-claim evidence checking.

**Scaffold.** Scores each claim by lexical overlap with the chunks it cites. That
is a placeholder for entailment, not an approximation of it: overlap cannot tell
"X is required" from "X is not required", which is precisely the failure mode the
product exists to catch.

Week 6 replaces :func:`score_claim` with an NLI model (mDeBERTa-based, multilingual)
scoring ``P(entailment | premise=cited chunks, hypothesis=claim)``, batched and
quantized to stay inside the latency budget, with a cascade so that only
low-confidence claims pay for full verification.

The rule that survives into the real implementation: **a claim with no citation is
unsupported, whatever it says.** Plausibility is not evidence.
"""

from __future__ import annotations

import re

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

app = create_app("verifier")

_WORD = re.compile(r"\w+", re.UNICODE)

# Words carrying no evidential weight in either language. A short list on purpose:
# a long stopword list tuned by hand is a way to overfit a placeholder.
_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "for", "and", "or", "by", "is", "are", "this", "that",
    "ve", "veya", "ile", "bir", "bu", "şu", "için", "olarak", "de", "da",
}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text)} - _STOPWORDS


def score_claim(claim: DraftClaim, chunks_by_id: dict[str, Chunk]) -> float:
    """Return a support score in [0, 1] for one claim.

    Replaced by NLI entailment in week 6; the signature is the seam.
    """
    if not claim.citations:
        return 0.0

    premise = " ".join(
        chunks_by_id[cid].text for cid in claim.citations if cid in chunks_by_id
    )
    if not premise:
        # Cited something that was not retrieved: a dangling citation is worse
        # than no citation, because it looks like evidence.
        return 0.0

    hypothesis_tokens = _tokens(claim.text)
    if not hypothesis_tokens:
        return 0.0
    return len(hypothesis_tokens & _tokens(premise)) / len(hypothesis_tokens)


@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest) -> VerifyResponse:
    settings = get_settings()
    chunks_by_id = {c.chunk_id: c for c in req.chunks}

    verified = []
    for draft in req.claims:
        score = score_claim(draft, chunks_by_id)
        verified.append(
            Claim(
                text=draft.text,
                citations=draft.citations,
                support_score=round(score, 4),
                status=(
                    ClaimStatus.SUPPORTED
                    if score >= settings.support_threshold
                    else ClaimStatus.UNSUPPORTED
                ),
            )
        )
    return VerifyResponse(claims=verified)
