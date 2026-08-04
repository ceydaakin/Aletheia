"""Generation service — citation-constrained answer drafting.

**Scaffold.** Produces a draft from the retrieved chunks without calling an LLM,
so the pipeline runs offline and deterministically. Week 5 replaces the body of
:func:`generate` with:

* prompt construction that carries document text on an ``untrusted`` channel,
  so a chunk cannot issue instructions (PRD §4.2, security);
* grammar-constrained decoding that forces every sentence to end with the chunk
  ids it used, which is what makes per-claim verification possible at all;
* claim decomposition (sentence split plus conjunction splitting).

The stub deliberately emits one uncited claim. A generator that always cites
perfectly would let a broken verifier look correct.
"""

from __future__ import annotations

from aletheia.contracts import DraftClaim, GenerateRequest, GenerateResponse
from aletheia.service import create_app

app = create_app("generation")


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    if not req.chunks:
        return GenerateResponse(answer="", claims=[])

    claims: list[DraftClaim] = [
        DraftClaim(text=_first_sentence(chunk.text), citations=[chunk.chunk_id])
        for chunk in req.chunks[:2]
    ]
    # One unsupported claim, so the verifier and the action policy have something
    # to do in a dev run.
    claims.append(
        DraftClaim(
            text="This also applies to public sector contracts without exception.",
            citations=[],
        )
    )

    return GenerateResponse(answer=" ".join(c.text for c in claims), claims=claims)


def _first_sentence(text: str) -> str:
    head, sep, _ = text.strip().partition(".")
    return (head + sep) if sep else head
