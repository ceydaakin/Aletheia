"""Ingestion worker — parse, chunk, embed, version.

**Scaffold.** Currently a NATS-shaped loop with no NATS: it logs its configuration
and idles so that ``docker compose up`` brings up the full topology. Week 2 fills
it in:

* Subscribe to ``aletheia-ingest`` on JetStream, with per-document ack so a crash
  mid-corpus resumes rather than restarts.
* Layout-aware parsing for PDF/HTML/Markdown/DOCX.
* Chunking that keeps ``(doc_id, version, span)`` so a citation points at a
  specific range of a specific version.
* Bitemporal writes: an amended document closes the previous row's validity
  interval instead of overwriting it (ADR-0002).

Ingestion is where the drift signal originates: a corpus change invalidates the
exchangeability assumption the guarantee rests on, so this worker is expected to
publish a recalibration trigger, not just write rows.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from aletheia.service import configure_logging
from aletheia.settings import get_settings

log = logging.getLogger("ingestion")


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    log.info(
        "ingestion worker started (scaffold: no work is consumed yet)",
        extra={
            "extra_fields": {
                "nats_url": settings.nats_url,
                "stream": settings.nats_ingest_stream,
                "embedding_model": settings.embedding_model,
            }
        },
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Not implemented on Windows dev boxes; there we fall back to the
        # KeyboardInterrupt path in main().
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("ingestion worker stopped")


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
