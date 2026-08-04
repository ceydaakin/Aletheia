# Architecture Decision Records

Each ADR captures one decision that was expensive to make and would be expensive to
reverse. Format is deliberately short: context, decision, consequences — including the
consequences we dislike.

| # | Title | Status |
|---|---|---|
| [0001](0001-system-architecture.md) | System architecture: Go gateway, Python model services | Accepted |
| [0002](0002-bitemporal-chunk-store.md) | Postgres + pgvector as the single store, bitemporal chunks | Accepted |
| [0003](0003-python-monorepo-one-image.md) | One Python distribution, many service entrypoints | Accepted |
| [0004](0004-risk-control-at-response-boundary.md) | Risk control applies to the whole response, not per claim | Accepted |

New ADR: copy the shape of 0001, take the next number, never edit an accepted one —
supersede it with a new record and mark the old one `Superseded by ADR-NNNN`.
