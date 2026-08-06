"""Load documents from the command line.

    python -m aletheia.ingestion.cli load ./corpora/tr-mevzuat --tenant demo
    python -m aletheia.ingestion.cli load ./x.pdf --tenant demo --valid-from 2024-01-01
    python -m aletheia.ingestion.cli list --tenant demo

Writes straight to Postgres, bypassing NATS. That is the point: corpus loading and
eval runs want a synchronous result and a non-zero exit code on failure, not a job
id to poll. The queue path exists for the API, where a caller must not wait on a
300-page filing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from aletheia.db import Database, configure_event_loop
from aletheia.embedding import get_embedder
from aletheia.ingestion import pipeline
from aletheia.ingestion.messages import IngestMessage
from aletheia.ingestion.parsing import MEDIA_TYPES
from aletheia.ingestion.store import IngestError, Outcome
from aletheia.service import configure_logging
from aletheia.settings import get_settings


def _documents(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_TYPES)


def _doc_id(path: Path, root: Path) -> str:
    """Stable id derived from the path relative to the corpus root.

    Re-running a load over the same tree must hit the same documents, or every
    run would create a parallel corpus instead of amending the existing one.
    """
    relative = path.relative_to(root) if path != root else Path(path.name)
    return relative.as_posix()


async def _tenant_exists(db: Database, tenant_id: str) -> bool:
    async with db.connection() as conn:
        cur = await conn.execute("SELECT 1 FROM tenants WHERE tenant_id = %s", (tenant_id,))
        return await cur.fetchone() is not None


async def _load(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    target = Path(args.path).resolve()
    if not target.exists():
        print(f"no such path: {target}", file=sys.stderr)
        return 2

    paths = _documents(target)
    if not paths:
        print(f"no supported documents under {target}", file=sys.stderr)
        print(f"supported extensions: {', '.join(sorted(MEDIA_TYPES))}", file=sys.stderr)
        return 2

    root = target if target.is_dir() else target.parent
    valid_from = (
        datetime.fromisoformat(args.valid_from).replace(tzinfo=UTC)
        if args.valid_from
        else datetime.now(UTC)
    )

    db = Database(args.database_url or settings.database_url)
    await db.open()

    if not await _tenant_exists(db, args.tenant):
        # Otherwise this surfaces as a foreign-key violation on the first
        # document, which says what broke but not what to do about it.
        print(f"unknown tenant {args.tenant!r}", file=sys.stderr)
        print(
            "Create it first:\n"
            "  INSERT INTO tenants (tenant_id, name, api_key_hash) "
            f"VALUES ('{args.tenant}', '{args.tenant}', 'replace-me');",
            file=sys.stderr,
        )
        await db.close()
        return 2

    embedder = get_embedder(settings)

    counts: dict[str, int] = {}
    failures = 0
    try:
        for path in paths:
            message = IngestMessage(
                job_id=f"cli_{uuid.uuid4().hex[:12]}",
                tenant_id=args.tenant,
                doc_id=args.doc_id or _doc_id(path, root),
                filename=path.name,
                lang=args.lang,
                source_uri=path.as_uri(),
                valid_from=valid_from,
                correction=args.correction,
            )
            try:
                result = await pipeline.run(db, message, settings=settings, embedder=embedder)
            except IngestError as exc:
                failures += 1
                print(f"FAIL  {path.name}: {exc}", file=sys.stderr)
                continue

            counts[str(result.outcome)] = counts.get(str(result.outcome), 0) + 1
            marker = "  ok" if result.outcome is not Outcome.UNCHANGED else "same"
            print(f"{marker}  {message.doc_id}  v{result.version}  {result.chunk_count} chunks")
    finally:
        await db.close()

    summary = ", ".join(f"{n} {name}" for name, n in sorted(counts.items())) or "nothing"
    print(f"\n{len(paths)} file(s): {summary}" + (f", {failures} failed" if failures else ""))
    return 1 if failures else 0


async def _list(args: argparse.Namespace) -> int:
    settings = get_settings()
    db = Database(args.database_url or settings.database_url)
    await db.open()
    try:
        async with db.connection() as conn:
            cur = await conn.execute(
                """
                SELECT doc_id, version, chunk_count, lang, valid_from
                FROM current_documents WHERE tenant_id = %s ORDER BY doc_id
                """,
                (args.tenant,),
            )
            rows = await cur.fetchall()
    finally:
        await db.close()

    if not rows:
        print(f"no documents in force for tenant {args.tenant!r}")
        return 0
    for row in rows:
        print(
            f"{row['doc_id']:<50} v{row['version']:<4} "
            f"{row['chunk_count']:>5} chunks  {row['valid_from']:%Y-%m-%d}  {row['lang']}"
        )
    print(f"\n{len(rows)} document(s) in force")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aletheia-ingest", description=__doc__)
    parser.add_argument("--database-url", default="", help="overrides DATABASE_URL")
    sub = parser.add_subparsers(dest="command", required=True)

    load = sub.add_parser("load", help="ingest a file or a directory tree")
    load.add_argument("path")
    load.add_argument("--tenant", required=True)
    load.add_argument("--lang", default="", help="ISO code, e.g. tr or en")
    load.add_argument(
        "--doc-id", default="", help="override the derived id (single files only)"
    )
    load.add_argument(
        "--valid-from",
        default="",
        help="ISO date this text took effect in the world; defaults to now",
    )
    load.add_argument(
        "--correction",
        action="store_true",
        help="the current version was never correct — supersede it rather than "
        "amending. Wrong here erases the period the old text held (ADR-0005).",
    )
    load.set_defaults(func=_load)

    listing = sub.add_parser("list", help="show the documents in force")
    listing.add_argument("--tenant", required=True)
    listing.set_defaults(func=_list)

    args = parser.parse_args(argv)
    if args.command == "load" and args.doc_id and Path(args.path).is_dir():
        parser.error("--doc-id cannot be used with a directory")
    configure_event_loop()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
