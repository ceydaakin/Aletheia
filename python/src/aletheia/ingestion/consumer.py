"""NATS JetStream consumer.

At-least-once delivery with explicit ack, so a worker crash mid-corpus resumes
rather than restarts. Ingestion is idempotent by content hash (ADR-0005), so a
redelivered message that already succeeded is a no-op rather than a duplicate
version — which is what makes at-least-once safe here.

Poison messages are the failure mode that actually bites: one unparseable PDF
redelivered forever blocks every document behind it. Past
``ingest_max_attempts`` a message is terminated, the job is marked failed, and
the queue moves on.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import nats
from nats.js.api import ConsumerConfig, RetentionPolicy, StreamConfig
from nats.js.errors import BadRequestError
from pydantic import ValidationError

from aletheia.db import Database
from aletheia.embedding import Embedder
from aletheia.ingestion import jobs, pipeline
from aletheia.ingestion.messages import IngestMessage
from aletheia.ingestion.store import IngestError
from aletheia.metrics import registry
from aletheia.settings import Settings

log = logging.getLogger("ingestion.consumer")

METRIC_PROCESSED = "aletheia_ingest_documents_total"
METRIC_DURATION = "aletheia_ingest_duration_seconds"


class Consumer:
    def __init__(self, db: Database, settings: Settings, embedder: Embedder) -> None:
        self._db = db
        self._settings = settings
        self._embedder = embedder
        self._nc: nats.NATS | None = None
        self._js = None
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.connected = False

    async def connect(self) -> None:
        self._nc = await nats.connect(
            self._settings.nats_url,
            max_reconnect_attempts=-1,
            reconnect_time_wait=2,
        )
        self._js = self._nc.jetstream()
        # Already exists with a different but compatible config.
        with contextlib.suppress(BadRequestError):
            await self._js.add_stream(
                StreamConfig(
                    name=self._settings.nats_ingest_stream,
                    subjects=[f"{self._settings.ingest_subject}.>"],
                    retention=RetentionPolicy.WORK_QUEUE,
                )
            )
        self.connected = True
        log.info("connected to NATS", extra={"extra_fields": {"url": self._settings.nats_url}})

    async def publish(self, message: IngestMessage) -> None:
        if self._js is None:
            raise RuntimeError("consumer is not connected")
        subject = f"{self._settings.ingest_subject}.{message.tenant_id}"
        await self._js.publish(subject, message.model_dump_json().encode())

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="ingest-consumer")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._nc is not None:
            await self._nc.drain()
        self.connected = False

    async def _run(self) -> None:
        assert self._js is not None
        subscription = await self._js.pull_subscribe(
            f"{self._settings.ingest_subject}.>",
            durable=self._settings.ingest_durable,
            config=ConsumerConfig(
                max_deliver=self._settings.ingest_max_attempts + 1,
                ack_wait=120,
            ),
        )
        log.info("ingest consumer ready")

        while not self._stopping.is_set():
            try:
                messages = await subscription.fetch(batch=1, timeout=5)
            except TimeoutError:
                continue
            except Exception:
                if self._stopping.is_set():
                    break
                log.warning("fetch failed; retrying", exc_info=True)
                await asyncio.sleep(1)
                continue

            for raw in messages:
                await self._handle(raw)

    async def _handle(self, raw) -> None:
        try:
            message = IngestMessage.model_validate_json(raw.data)
        except ValidationError:
            # Malformed and unfixable by retrying.
            log.error("discarding malformed ingest message", exc_info=True)
            registry.inc(METRIC_PROCESSED, outcome="malformed")
            await raw.term()
            return

        loop = asyncio.get_running_loop()
        started = loop.time()
        attempts = await jobs.mark_running(self._db, message.job_id)

        try:
            result = await pipeline.run(
                self._db, message, settings=self._settings, embedder=self._embedder
            )
        except IngestError as exc:
            # Deterministic failure: unsupported format, empty text, bad interval.
            # Retrying cannot help.
            log.error(
                "ingest rejected",
                extra={"extra_fields": {"job_id": message.job_id, "doc_id": message.doc_id, "error": str(exc)}},
            )
            await jobs.mark_failed(self._db, message.job_id, str(exc))
            registry.inc(METRIC_PROCESSED, outcome="rejected")
            await raw.term()
            return
        except Exception as exc:
            # Possibly transient — a database blip, a full disk. Worth a retry,
            # but not forever.
            log.exception("ingest failed", extra={"extra_fields": {"job_id": message.job_id}})
            if attempts >= self._settings.ingest_max_attempts:
                await jobs.mark_failed(
                    self._db, message.job_id, f"giving up after {attempts} attempts: {exc}"
                )
                registry.inc(METRIC_PROCESSED, outcome="exhausted")
                await raw.term()
            else:
                registry.inc(METRIC_PROCESSED, outcome="retry")
                await raw.nak(delay=min(2**attempts, 30))
            return

        await jobs.mark_succeeded(self._db, message.job_id, result)
        registry.observe(METRIC_DURATION, loop.time() - started)
        registry.inc(METRIC_PROCESSED, outcome=str(result.outcome))
        await raw.ack()
