# Aletheia model services

One distribution, five entrypoints (ADR-0003).

```bash
python -m venv .venv
.venv/Scripts/activate            # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

pytest
ruff check .

uvicorn aletheia.retrieval.app:app  --port 8001 --reload
uvicorn aletheia.generation.app:app --port 8002 --reload
uvicorn aletheia.verifier.app:app   --port 8003 --reload
uvicorn aletheia.risk.app:app       --port 8004 --reload
uvicorn aletheia.ingestion.app:app  --port 8005 --reload
```

Only `models` (torch, transformers, sentence-transformers) is an extra; storage and
document parsers are core dependencies, because by week 7 every service needs them and
they are small.

Loading documents without the queue:

```bash
python -m aletheia.ingestion.cli load ../corpora/demo --tenant demo --lang en
python -m aletheia.ingestion.cli list --tenant demo
```

Store tests need Postgres and skip without `ALETHEIA_TEST_DATABASE_URL`; see the root
README. On Windows, `aletheia.db.configure_event_loop()` must run before any loop is
created — psycopg's async mode cannot use the default ProactorEventLoop. The CLI and
the test suite both call it; a new entrypoint must too.

## Rules that outlive the stubs

- Services talk over HTTP using `aletheia.contracts`, and never import each
  other. `tests/test_architecture.py` enforces it.
- Every model rejects unknown fields. Silent tolerance is how two services drift
  apart while both look healthy.
- A claim with no citation is unsupported, whatever it says.
- No response quotes a bound it did not earn — see the `UNCALIBRATED SCAFFOLD`
  guarantee string the risk service currently returns.
- A chunk's span must reproduce its own text: `chunk.text == source[start:end]`.
  A citation that does not point at what it claims to is worse than no citation.
- Nothing in the corpus is ever deleted or overwritten. Amendments close a validity
  interval; corrections retract knowledge. Both keep the old rows.
