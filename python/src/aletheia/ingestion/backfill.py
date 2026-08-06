"""Embed chunks that were ingested without vectors.

    python -m aletheia.ingestion.backfill --tenant demo

The null embedding backend is the default (ADR-0005), so a corpus loaded on a
clean machine is lexically retrievable and densely invisible. This turns the
dense arm on without re-ingesting anything: chunk ids, spans, and versions are
untouched, so no citation changes and no corpus event fires — embedding a chunk
is not a change to what the corpus says.

Resumable by construction: the work queue is ``embedding IS NULL``, so an
interrupted run picks up where it stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from aletheia.db import Database, configure_event_loop
from aletheia.embedding import NullEmbedder, get_embedder
from aletheia.service import configure_logging
from aletheia.settings import get_settings


async def _pending(db: Database, tenant_id: str, limit: int) -> list[dict]:
    async with db.connection() as conn:
        cur = await conn.execute(
            """
            SELECT chunk_id, text FROM chunks
            WHERE tenant_id = %s AND embedding IS NULL
            ORDER BY chunk_id
            LIMIT %s
            """,
            (tenant_id, limit),
        )
        return await cur.fetchall()


async def _count_pending(db: Database, tenant_id: str) -> int:
    async with db.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM chunks WHERE tenant_id = %s AND embedding IS NULL",
            (tenant_id,),
        )
        return int((await cur.fetchone())["n"])


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    embedder = get_embedder(settings)
    if isinstance(embedder, NullEmbedder):
        print(
            "EMBEDDING_BACKEND is 'null', so this would write nothing.\n"
            "Set EMBEDDING_BACKEND=sentence-transformers and install the models "
            'extra: pip install -e ".[models]"',
            file=sys.stderr,
        )
        return 2

    db = Database(args.database_url or settings.database_url)
    await db.open()
    try:
        total = await _count_pending(db, args.tenant)
        if not total:
            print(f"nothing to embed for tenant {args.tenant!r}")
            return 0
        print(f"{total} chunk(s) to embed with {embedder.name} ({settings.embedding_model})")

        done = 0
        started = time.perf_counter()
        while True:
            batch = await _pending(db, args.tenant, args.batch_size)
            if not batch:
                break

            vectors = embedder.embed([row["text"] for row in batch])
            rows = [
                ("[" + ",".join(f"{v:.6g}" for v in vector) + "]", row["chunk_id"], args.tenant)
                for row, vector in zip(batch, vectors, strict=True)
                if vector is not None
            ]
            if not rows:
                print("embedder returned no vectors; stopping", file=sys.stderr)
                return 1

            # One transaction per batch: an interrupted run leaves committed
            # batches embedded and the rest still queued, rather than rolling back
            # an hour of GPU time.
            async with db.transaction() as conn, conn.cursor() as cur:
                await cur.executemany(
                    "UPDATE chunks SET embedding = %s::vector WHERE chunk_id = %s AND tenant_id = %s",
                    rows,
                )

            done += len(rows)
            rate = done / max(time.perf_counter() - started, 1e-6)
            print(f"  {done}/{total} ({rate:.0f} chunks/s)", flush=True)

        print(f"embedded {done} chunk(s)")
        return 0
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aletheia-backfill", description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--database-url", default="", help="overrides DATABASE_URL")
    args = parser.parse_args(argv)

    configure_event_loop()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
