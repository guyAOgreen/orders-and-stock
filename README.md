# orders-and-stock

[![CI](https://github.com/guyAOgreen/orders-and-stock/actions/workflows/ci.yml/badge.svg)](https://github.com/guyAOgreen/orders-and-stock/actions/workflows/ci.yml)

Mr D take-home assignment: a minimal orders flow that updates stock, built with
Python and PostgreSQL. The brief is in [docs/Requirements.pdf](docs/Requirements.pdf).

## Status

The project skeleton and the database schema are in place: FastAPI with
SQLAlchemy, Psycopg and Alembic, a PostgreSQL 17 container, separate API and
stock worker entry points, and migrations for products, orders, order items and
stock work. The API serves a health endpoint and the worker starts, checks the
database and exits. Order intake, stock processing and the daily report are
implemented in the follow-up issues. See [SOLUTION.md](SOLUTION.md) for the
design and [AGENTS.md](AGENTS.md) for the development rules.

## Prerequisites

- Linux or macOS (development on Windows works too; see the note below).
- [uv](https://docs.astral.sh/uv/) — installs Python 3.12 and all
  dependencies: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker with Compose, for PostgreSQL. Without Docker, use any PostgreSQL 17
  server: create databases `orders_stock` and `orders_stock_test` and point the
  URLs in `.env` at them.

## Setup

```bash
cp .env.example .env            # connection URLs matching docker-compose.yml
docker compose up -d --wait     # PostgreSQL 17 on localhost:5433; waits until it is ready
uv sync                         # creates .venv with Python 3.12 and all dependencies
uv run alembic upgrade head     # applies the schema to the development database
```

Both the container and the application read their credentials from `.env`,
so copy it before starting Compose. The values in `.env.example` are for local
development only. PostgreSQL is published on host port 5433 rather than 5432
so it does not clash with a locally installed server. The test database
`orders_stock_test` is created automatically the first time the container
starts.

## Run

Two processes, two terminals:

```bash
uv run orders-stock-api         # http://127.0.0.1:8000, OpenAPI docs at /docs
uv run orders-stock-worker      # the stock worker
```

For development with auto-reload, `uv run fastapi dev` serves the same
application. `API_HOST`, `API_PORT` and `LOG_LEVEL` can be set in `.env`.

Try it: `curl http://127.0.0.1:8000/health` returns `{"status":"ok"}`.

## Test and check

```bash
uv run pytest                   # needs the PostgreSQL container running
uv run ruff format --check .    # formatting
uv run ruff check .             # linting
uv run mypy                     # type checking (strict)
```

Integration tests run against `orders_stock_test`, migrate it to head once per
session and truncate tables between tests. They fail rather than skip if the
database is unreachable.

The same checks run in GitHub Actions on every pull request and push to
`main`, against a PostgreSQL 17 service container
([ci.yml](.github/workflows/ci.yml)).

## Project layout

| Path | Purpose |
|---|---|
| `src/orders_stock/api/` | FastAPI application factory and the API entry point |
| `src/orders_stock/orders/` | Orders capability |
| `src/orders_stock/stock/` | Stock capability |
| `src/orders_stock/worker_main.py` | Stock worker entry point |
| `src/orders_stock/models.py` | SQLAlchemy models for all tables; the schema is described in [SOLUTION.md](SOLUTION.md#schema) |
| `src/orders_stock/config.py`, `db.py`, `logging_config.py` | Settings, database engine/session factory, logging setup |
| `alembic/` | Migrations; `alembic/env.py` takes the URL from settings |
| `docker-compose.yml`, `docker/` | PostgreSQL container and its init script |
| `tests/` | pytest suite; fixtures in `tests/conftest.py` |

## Windows note

Development on Windows works with the same commands, except that some
environments block uv's console-script launchers. The module forms are
equivalent: `uv run python -m orders_stock.api.main` and
`uv run python -m orders_stock.worker_main`. Enable Developer Mode and run
`git config --global core.symlinks true` before cloning, otherwise the
`.claude/skills` symlink is checked out as a text file.

## AI-assisted development

| File | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | Shared instructions: scope, conventions, testing, workflow |
| [CLAUDE.md](CLAUDE.md) | Imports `AGENTS.md` and adds Claude Code notes |
| [.agents/skills/](.agents/skills/) | Project skills, with sources in [SOURCES.md](.agents/skills/SOURCES.md) |
| `.claude/skills` | Symlink to `.agents/skills` |

### Instructions and skills

Codex reads `AGENTS.md` and `.agents/skills/` from the repository. Claude Code
reads `CLAUDE.md` and `.claude/skills/`. Neither needs configuring on Linux or
macOS.

### Superpowers

Development follows the [Superpowers](https://github.com/obra/superpowers)
workflow.

- Claude Code: run `/plugin install superpowers@claude-plugins-official`.
- Codex: follow the Codex install instructions in the Superpowers README.
