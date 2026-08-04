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
RUN pip install --no-cache-dir .

# Model extras are deliberately NOT installed here: the scaffold runs without
# torch, and a several-gigabyte image would make `docker compose up` on a clean
# machine (G6) a much worse first experience. Week 3 adds a `models` stage that
# layers them on for the services that need them.

RUN useradd --create-home --uid 10001 aletheia
USER aletheia

# Overridden per service in docker-compose.yml.
CMD ["uvicorn", "aletheia.retrieval.app:app", "--host", "0.0.0.0", "--port", "8001"]
