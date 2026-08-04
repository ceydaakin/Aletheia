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
python -m aletheia.ingestion.worker
```

Extras: `db` (psycopg + pgvector), `models` (torch, transformers), `ingest`
(NATS, parsers). None are needed to run or test the scaffold.

## Rules that outlive the stubs

- Services talk over HTTP using `aletheia.contracts`, and never import each
  other. `tests/test_architecture.py` enforces it.
- Every model rejects unknown fields. Silent tolerance is how two services drift
  apart while both look healthy.
- A claim with no citation is unsupported, whatever it says.
- No response quotes a bound it did not earn — see the `UNCALIBRATED SCAFFOLD`
  guarantee string the risk service currently returns.
