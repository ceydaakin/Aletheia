"""Environment-driven configuration, shared by every service."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: str = "info"

    # Tracing. Empty endpoint installs the propagator but exports nothing, so
    # trace context still flows downstream. See aletheia.tracing.
    otlp_endpoint: str = ""
    trace_sample_ratio: float = 1.0

    database_url: str = "postgresql://aletheia:aletheia@postgres:5432/aletheia"
    nats_url: str = "nats://nats:4222"
    nats_ingest_stream: str = "aletheia-ingest"

    # Ingestion.
    ingest_subject: str = "ingest.document"
    ingest_durable: str = "aletheia-ingest-worker"
    # A document that keeps failing is a poison message. Past this many
    # redeliveries it is marked failed and dropped, because one unparseable PDF
    # must not block the rest of the corpus.
    ingest_max_attempts: int = 3
    chunk_target_chars: int = 1200
    chunk_overlap_chars: int = 150
    chunk_min_chars: int = 120

    # Embedding backend: "null" stores no vectors and leaves chunks lexically
    # retrievable; "sentence-transformers" needs the `models` extra. Backfill
    # existing chunks with `python -m aletheia.ingestion.backfill`.
    embedding_backend: str = "null"

    # Retrieval. The arms are deliberately wider than what generation sees:
    # recall cannot be recovered downstream, precision can (ADR-0006).
    retrieval_lexical_k: int = 50
    retrieval_dense_k: int = 50
    retrieval_rrf_k: int = 60
    retrieval_top_n: int = 6
    """How many chunks generation actually receives."""

    # Reranker backend: "null" preserves fusion order and keeps the stack
    # runnable without torch; "cross-encoder" needs the `models` extra.
    reranker_backend: str = "null"
    rerank_max_candidates: int = 32
    """Cross-encoding is one forward pass per candidate. This cap is what keeps
    the retrieval stage inside its 1.2 s budget."""

    # Models. Mostly unused by the scaffold; wired up from week 3 onwards.
    embedding_model: str = "intfloat/multilingual-e5-base"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    nli_model: str = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
    llm_model: str = "claude-sonnet-5"

    # Generation backend: "extractive" selects sentences from the retrieved
    # chunks — deterministic, no key, and *incapable of hallucinating*, so any
    # bound calibrated against it measures retrieval and verifier strictness
    # rather than unsupported generation. "anthropic" needs ANTHROPIC_API_KEY.
    generation_backend: str = "extractive"
    generation_max_claims: int = 4

    # Risk control.
    default_risk_budget: float = 0.05
    risk_confidence: float = 0.95
    # Beyond this age a calibration no longer describes the corpus it was fitted
    # on, and the guarantee it backs is not one we are willing to quote.
    calibration_max_age_hours: int = 168

    # Verifier. "overlap" is the model-free baseline and an ablation row; "nli"
    # scores P(entailment) with a multilingual model and needs the `models` extra.
    verifier_backend: str = "overlap"
    verifier_batch_size: int = 16
    # int8 dynamic quantization. Roughly halves CPU inference time; the accuracy
    # cost must be re-measured, because the guarantee rests on this model telling
    # a claim from its contradiction.
    verifier_quantize: bool = False
    # Premises are single cited chunks (~1200 chars), so 256 tokens covers them
    # and attention cost is quadratic in this number.
    verifier_max_length: int = 256
    # A claim scoring at or above this is treated as supported. This is *not* the
    # risk threshold — that one is calibrated (ADR-0004). This only decides the
    # per-claim label the action policy acts on.
    support_threshold: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
