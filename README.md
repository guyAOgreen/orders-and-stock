# orders-and-stock

[![CI](https://github.com/guyAOgreen/orders-and-stock/actions/workflows/ci.yml/badge.svg)](https://github.com/guyAOgreen/orders-and-stock/actions/workflows/ci.yml)

Mr D take-home assignment: a minimal orders flow that updates stock, built with
Python and PostgreSQL. The brief is in [docs/Requirements.pdf](docs/Requirements.pdf).

## Status

The schema, the Orders intake API, the stock worker and the seed/burst
command are in place: FastAPI with SQLAlchemy, Psycopg and Alembic, a
PostgreSQL 17 container, separate API and stock worker entry points,
migrations for products, orders, order items and stock work, `POST /orders`
and `GET /orders/{order_ref}` with idempotent acceptance by `order_ref`, a
worker that applies pending stock work one order per transaction,
`GET /stock?sku=...` for current stock, and `orders-stock-seed-burst` to seed
products and submit a burst of orders with duplicates. The daily report is
implemented in the follow-up issue. See [SOLUTION.md](SOLUTION.md) for the design and
[AGENTS.md](AGENTS.md) for the development rules.

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
application. `API_HOST`, `API_PORT`, `LOG_LEVEL` and
`WORKER_POLL_INTERVAL_SECONDS` (how long the worker waits when it finds no
pending work; default 1 second) can be set in `.env`.

The worker logs each order it applies and stops cleanly on Ctrl+C (SIGINT) or
SIGTERM: the order being applied is committed or rolled back whole, no
further order is started, and the process exits with status 0.

Try it: `curl http://127.0.0.1:8000/health` returns `{"status":"ok"}`.

### Seed and burst

With the API running, `orders-stock-seed-burst` seeds four products straight
into the database and then submits a short burst of eight orders through the
API, two of which repeat an earlier `order_ref`:

```bash
uv run orders-stock-seed-burst          # API at http://127.0.0.1:8000
```

```
Seeded 4 products (4 new, 0 existing)
web-100045 -> 201 new
web-100046 -> 201 new
web-100045 -> 200 duplicate
web-100047 -> 201 new
web-100048 -> 201 new
web-100049 -> 201 new
web-100046 -> 200 duplicate
web-100050 -> 201 new
Submitted 8 orders: 6 new, 2 duplicates
```

Run it again and the seed reports `0 new, 4 existing` and every order comes
back as a duplicate: products are never duplicated, existing stock levels are
left alone, and the same `order_ref` is never counted twice. Options:

- `--batch <label>` prefixes the refs (`<label>-100045`, ...) to submit a
  fresh set, for example while the worker is stopped.
- `--seed-only` / `--burst-only` run one phase.
- `--api-url <url>` targets an API elsewhere; `DATABASE_URL` must point at the
  database that API uses.

If the API cannot be reached, or answers with anything other than `201` or
`200`, the command prints the error and exits with status 1. A timeout can
land after an order was accepted, so the recovery is simply to re-run the same
batch: the accepted orders come back as duplicates. The seeded stock covers
the documented burst and demonstration; it is not replenished, and the seed
never resets it. To reset a level, update the `products` table directly.

### Orders API

Create an order. Prices are read at acceptance and copied onto the items:

```bash
curl -i -X POST http://127.0.0.1:8000/orders -H 'content-type: application/json' \
  -d '{"order_ref":"web-100099","customer_id":"cust-42",
       "items":[{"sku":"BAN-001","qty":2},{"sku":"MLK-002","qty":1}]}'
```

```
HTTP/1.1 201 Created
{"order_ref":"web-100099","customer_id":"cust-42",
 "items":[{"sku":"BAN-001","qty":2,"unit_price_cents":199},
          {"sku":"MLK-002","qty":1,"unit_price_cents":2599}],
 "total_cents":2997,"status":"accepted","stock_status":"pending",
 "accepted_at":"2026-10-06T17:28:40.003164Z"}
```

Send the same request again and the response is `200 OK` with the same order;
nothing is written. `GET /orders/web-100099` returns the same body. `status`
is the acceptance status and `stock_status` is `pending` until the worker
applies the stock decrement, then `applied`. An unknown SKU is rejected with
`422` and `{"detail":"Unknown SKU(s): NOPE-000"}`; an unknown `order_ref` on
`GET` is `404`. An `order_ref` may contain letters, digits, `.`, `_`, `~` and
`-` (up to 128 characters, not only dots) so it can appear unencoded in the
`GET` path. The interactive documentation at `/docs` lists every route.

### Stock API

Current stock for a SKU, read live:

```bash
curl -s 'http://127.0.0.1:8000/stock?sku=BAN-001'
```

```
{"sku":"BAN-001","name":"Bananas 1kg","stock":489}
```

That is the seeded level of 500 less the 11 bananas in the seed/burst
orders, once the worker has applied them; while they are pending it is
still 500.

An unknown SKU is `404` with `{"detail":"Unknown SKU"}`; a missing or empty
`sku` is `422`. The SKU is a query parameter rather than a path segment so
that any catalogue SKU, including one containing `/` or `.`, can be read.

### Demonstrating the interruption and catch-up

Stock is applied by the worker, so stopping the worker simulates the stock
capability being unavailable while the API keeps accepting orders. With
PostgreSQL and the API running:

1. **Stop the worker** (Ctrl+C in its terminal), or do not start it yet.
2. **Submit orders.** A fresh batch from the seed/burst command is the
   quickest way; a hand-written `POST /orders` works just as well:

   ```bash
   uv run orders-stock-seed-burst --burst-only --batch demo2
   ```

   Every order is accepted as usual, six new and two duplicates.
3. **Observe the backlog.** `GET /orders/demo2-100045` reports
   `"stock_status":"pending"` and `GET /stock?sku=BAN-001` is unchanged. The
   pending work is in PostgreSQL, so it survives any restart.
4. **Restart the worker:** `uv run orders-stock-worker`. It logs
   `applied stock for order_id=...` for each backlog order, oldest first.
5. **Observe the catch-up.** The orders now report `"stock_status":"applied"`
   and `GET /stock?sku=BAN-001` has dropped by the 11 bananas in the batch.

Submitting the same `order_ref` again at any point returns `200` with the
existing order and leaves stock untouched, which is the duplicate path.

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
| `src/orders_stock/api/` | FastAPI application factory, the session dependency and the API entry point |
| `src/orders_stock/orders/` | Orders capability: request/response schemas, the acceptance service and its router |
| `src/orders_stock/stock/` | Stock capability: the worker loop (`worker.py`), the stock router and its schemas |
| `src/orders_stock/worker_main.py` | Stock worker entry point: startup check, signal handling, the polling loop |
| `src/orders_stock/seed_burst.py` | The seed and burst command |
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
