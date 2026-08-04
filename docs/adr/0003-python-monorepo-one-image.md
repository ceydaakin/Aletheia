# ADR-0003 — One Python distribution, many service entrypoints

- **Status:** Accepted
- **Date:** 2026-08-04
- **Relates to:** ADR-0001, PRD §5.1, G6 (reproducibility)

## Context

ADR-0001 gives us five Python components: retrieval, generation, verifier, risk, and the
ingestion worker. The conventional microservice layout gives each its own repository or at
least its own `pyproject.toml`, lockfile, and Dockerfile. With five components and one
developer working ~12 hours a week for 12 weeks, that is five dependency graphs to keep
in sync for a system where all five share the same contracts, the same settings loader,
the same logging setup, and the same database access layer.

Independent deployability — the thing that justifies that cost — is not actually needed
here. All five ship together, from one commit, on one node.

## Decision

**One Python package (`aletheia`), one `pyproject.toml`, one image; the entrypoint selects
the service.**

```
python/src/aletheia/
  contracts.py     # Pydantic models — the wire contract, shared by all
  settings.py      # env-driven config
  service.py       # FastAPI factory: /healthz, /readyz, /metrics, request logging
  db.py            # connection pool, temporal query helpers
  retrieval/app.py generation/app.py verifier/app.py risk/app.py
  ingestion/worker.py
```

Compose runs the same image five times with different commands. Heavy model dependencies
(`torch`, `sentence-transformers`) are declared as **optional extras** so that a developer
working on the gateway or the risk controller can `pip install -e ".[dev]"` and get a
working environment in seconds instead of pulling multi-gigabyte wheels.

Service boundaries stay real where it counts: services communicate only over HTTP using
`contracts.py` types, and never import each other's modules. `tests/test_architecture.py`
walks each sub-package's AST and fails the build on a cross-service import. The boundary
is architectural, not packaging-level.

## Consequences

**Good.** One dependency graph, one lockfile, one CI install step, one image build. A
change to a shared contract updates every consumer atomically in a single commit — no
version-skew window where the verifier speaks a schema the risk controller has not learned
yet. `docker compose up` on a clean machine (G6) has one Python build to get right instead
of five.

**Bad, and accepted.** The image carries every service's dependencies, so the risk
controller container ships `torch` it never imports. Cost is disk and pull time on a single
node, not runtime. If image size becomes a real problem, a multi-stage build with
per-service extras splits it without changing any application code.

**Bad.** The no-cross-import rule is a convention, and the test enforces the letter of it
(direct imports) rather than the spirit (shared mutable state, coupling through the
database). Someone — me, at week 9, at 1 a.m. — will eventually want
`aletheia.verifier.nli` from the risk service because it is right there. That is a genuine
risk of this layout, and the reason the rule is checked in CI rather than written in a
comment.

**Reversible.** Splitting later is mechanical: each sub-package already has its own
entrypoint and no inbound imports. This decision costs little to undo, which is the main
reason to take the cheap option now.
