"""Generation service — citation-constrained answer drafting.

Produces an answer whose every sentence carries the chunk ids it used, then
decomposes it into atomic claims. Both halves matter: the citation format is what
makes per-claim verification possible at all, and the decomposition granularity
decides what the guarantee is quantified over (see
:mod:`aletheia.generation.decompose`).

Backends are selected by ``GENERATION_BACKEND``. The default is extractive and
cannot hallucinate, which is convenient for reproducibility and important to
remember when reading any number calibrated against it — see
:mod:`aletheia.generation.providers`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aletheia.contracts import GenerateRequest, GenerateResponse
from aletheia.generation.providers import build_answer, get_generator
from aletheia.service import create_app
from aletheia.settings import get_settings

log = logging.getLogger("generation")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.generator = get_generator(settings)
    log.info(
        "generation started",
        extra={"extra_fields": {"backend": app.state.generator.name}},
    )
    yield


app = create_app("generation", lifespan=lifespan)


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    settings = get_settings()
    if not req.chunks:
        # Nothing retrieved means nothing to ground an answer in. Returning empty
        # lets the gateway abstain rather than inviting a model to fill the gap.
        return GenerateResponse(answer="", claims=[])

    answer, claims = build_answer(
        app.state.generator,
        req.query,
        req.chunks,
        max_claims=settings.generation_max_claims,
    )
    return GenerateResponse(answer=answer, claims=claims)
