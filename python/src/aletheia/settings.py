"""Environment-driven configuration, shared by every service."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: str = "info"

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
    # retrievable; "sentence-transformers" needs the `models` extra. Week 3
    # backfills the NULLs, so this is a config flip rather than a code change.
    embedding_backend: str = "null"

    # Models. Mostly unused by the scaffold; wired up from week 3 onwards.
    embedding_model: str = "intfloat/multilingual-e5-base"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    nli_model: str = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
    llm_provider: str = "stub"
    llm_model: str = "claude-sonnet-5"

    # Risk control.
    default_risk_budget: float = 0.05
    risk_confidence: float = 0.95
    # Beyond this age a calibration no longer describes the corpus it was fitted
    # on, and the guarantee it backs is not one we are willing to quote.
    calibration_max_age_hours: int = 168

    # Verifier. A claim scoring at or above this is treated as supported.
    # Provisional: the real value is whatever week 6 measures.
    support_threshold: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
