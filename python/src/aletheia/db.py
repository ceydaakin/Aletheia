"""Async Postgres access, shared by every service.

Thin on purpose: a pool, a transaction helper, and the temporal read paths. All
temporal access goes through :func:`chunks_as_of`, which calls the SQL function of
the same name — ad-hoc interval predicates in application code are how an
off-by-one silently serves a superseded chunk (ADR-0002).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from aletheia.settings import get_settings

log = logging.getLogger("db")


def configure_event_loop() -> None:
    """Select an event loop psycopg can actually use.

    Python 3.8+ defaults to ProactorEventLoop on Windows, and psycopg's async mode
    refuses to run on it. Production is Linux so this is a no-op there, but every
    developer machine here is Windows and the failure mode — a ten-second pool
    timeout with the real cause buried in a warning — is not one worth hitting
    twice.

    Must be called before the loop is created.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class Database:
    def __init__(self, url: str, *, min_size: int = 2, max_size: int = 32) -> None:
        """
        max_size is sized for fan-out, not for request count. Retrieval runs one
        arm per corpus language plus a dense arm concurrently, so a single request
        can hold several connections at once; a pool sized for the request rate
        alone starves under concurrency and surfaces as stage timeouts rather than
        as pool errors.
        """
        self._pool = AsyncConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            # Opening in the constructor would make importing a module do I/O.
            open=False,
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=10)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._pool.connection() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """One unit of work.

        Ingestion writes a whole document version inside one of these: a
        partially ingested document is a corpus state no calibration was fitted
        on (ADR-0005).
        """
        async with self._pool.connection() as conn, conn.transaction():
            yield conn

    async def healthy(self) -> bool:
        try:
            async with self.connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            log.warning("database health check failed", exc_info=True)
            return False


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database(get_settings().database_url)
    return _db


def set_db(db: Database | None) -> None:
    """Override the process-wide handle. Used by tests."""
    global _db
    _db = db


# --- Temporal reads --------------------------------------------------------


async def chunks_as_of(
    conn: AsyncConnection,
    tenant_id: str,
    valid_at: datetime | None = None,
    known_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Chunks in force at ``valid_at``, as far as we knew at ``known_at``.

    ``valid_at`` is what the API's ``as_of`` parameter selects. ``known_at`` is the
    auditor's question — "what would this system have said on 12 March?" — and
    defaults to now, meaning current knowledge.
    """
    cur = await conn.execute(
        "SELECT * FROM chunks_as_of(%s, COALESCE(%s, now()), COALESCE(%s, now()))",
        (tenant_id, valid_at, known_at),
    )
    return await cur.fetchall()


async def current_document(
    conn: AsyncConnection, tenant_id: str, doc_id: str
) -> dict[str, Any] | None:
    """The version in force now, as far as we currently know."""
    cur = await conn.execute(
        "SELECT * FROM current_documents WHERE tenant_id = %s AND doc_id = %s",
        (tenant_id, doc_id),
    )
    return await cur.fetchone()


async def latest_version(conn: AsyncConnection, tenant_id: str, doc_id: str) -> int:
    """Highest version number ever assigned, superseded or not.

    Versions never repeat — a chunk id must resolve to exactly one row forever,
    including rows a correction retired (ADR-0005).
    """
    cur = await conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM documents WHERE tenant_id = %s AND doc_id = %s",
        (tenant_id, doc_id),
    )
    row = await cur.fetchone()
    return int(row["v"]) if row else 0
