# syntax=docker/dockerfile:1
# One image for the API, the stock worker, migrations and the seed/burst
# command. Both stages use the same Python base so the virtual environment
# built in the first stage runs unchanged in the second.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /bin/

# Use the image's Python rather than a downloaded interpreter, compile
# bytecode at install time, and copy from the cache mount instead of linking.
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, from the lockfile only, so an application change does
# not reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# Then the project itself, as a regular (non-editable) package, so the
# runtime stage needs the virtual environment and nothing from src/.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE}

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv ./.venv
# Migrations are not part of the package; alembic.ini locates them with %(here)s.
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app

# Exec form: SIGTERM from `docker stop` reaches the application process itself.
CMD ["orders-stock-api"]
