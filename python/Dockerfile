# syntax=docker/dockerfile:1
#
# One image, five entrypoints (ADR-0003). Compose picks the service via `command`.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so a source-only change does not re-resolve the world.
COPY pyproject.toml ./
COPY src ./src
# The otel extra is small and pure-Python; the cut that matters is model weights.
RUN pip install --no-cache-dir ".[otel]"

RUN useradd --create-home --uid 10001 aletheia
USER aletheia

# Overridden per service in docker-compose.yml.
CMD ["uvicorn", "aletheia.retrieval.app:app", "--host", "0.0.0.0", "--port", "8001"]

# ---------------------------------------------------------------------------

# Model-backed variant. Not the default: torch plus the weights is several
# gigabytes, and `docker compose up` on a clean machine (PRD G6) should not pay
# for that before anyone has decided to use the dense arm or the NLI verifier.
#
# Build it with `docker compose build --build-arg TARGET=models`, or point a
# service at it explicitly. The weights themselves download on first use and
# belong on a mounted cache volume, not in the image.
FROM base AS models

USER root
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir ".[models]"
USER aletheia

ENV HF_HOME=/home/aletheia/.cache/huggingface

CMD ["uvicorn", "aletheia.verifier.app:app", "--host", "0.0.0.0", "--port", "8003"]
