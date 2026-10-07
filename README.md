# orders-and-stock

[![CI](https://github.com/guyAOgreen/orders-and-stock/actions/workflows/ci.yml/badge.svg)](https://github.com/guyAOgreen/orders-and-stock/actions/workflows/ci.yml)

Mr D take-home assignment: a minimal orders flow that updates stock, built with
Python and PostgreSQL. The brief is in [docs/Requirements.pdf](docs/Requirements.pdf).

## Status

Complete. An Orders API accepts orders idempotently by `order_ref` and
reports their status, a separate stock worker applies the stock decrements
from durable work rows in PostgreSQL, a stock endpoint reads current levels,
a daily report (Task 2, Option B) summarises a UTC day, and a seed/burst
command submits a burst of orders with duplicates. Everything runs natively
or in containers from one image. See [SOLUTION.md](SOLUTION.md) for the
design and [AGENTS.md](AGENTS.md) for the development rules.

## Prerequisites

- Linux or macOS (development on Windows works too; see the note below).
- [uv](https://docs.astral.sh/uv/) — installs Python 3.12 and all
  dependencies: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker with Compose, for PostgreSQL, and optionally to run the whole
  application in containers (then uv is not needed; see
  [Run in containers](#run-in-containers)). Without Docker, use any
  PostgreSQL 17 server: create databases `orders_stock` and
  `orders_stock_test` and point the URLs in `.env` at them.

## Setup

```bash
cp .env.example .env                  # connection URLs matching docker-compose.yml
docker compose up -d --wait postgres  # PostgreSQL 17 on localhost:5433; waits until it is ready
uv sync                               # creates .venv with Python 3.12 and all dependencies
uv run alembic upgrade head           # applies the schema to the development database
```

Both the container and the application read their credentials from `.env`,
so copy it before starting Compose. The values in `.env.example` are for local
development only; keep them to letters, digits, `_` and `-`, because they are
substituted verbatim into the connection URLs. PostgreSQL is published on
host port 5433 rather than 5432 so it does not clash with a locally installed
server. The test database
`orders_stock_test` is created automatically the first time the container
starts.

Name the `postgres` service as shown. A bare `docker compose up` also starts
the containerised API and worker, and a native API started afterwards would
find port 8000 taken.

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

### Seed and burst

`orders-stock-seed-burst` seeds four products straight into the database and
then submits a burst of eight orders through the API, two of which repeat an
earlier `order_ref`:

```bash
uv run orders-stock-seed-burst          # API at http://127.0.0.1:8000
```

Options:

- `--batch <label>` prefixes the refs (`<label>-100045`, ...) to submit a
  fresh set of orders. The default label is `web`.
- `--seed-only` / `--burst-only` run one phase.
- `--api-url <url>` targets an API elsewhere; `DATABASE_URL` must point at the
  database that API uses.

**Rerunning.** The order refs are fixed, so running the same batch again
creates no further orders: every request comes back as a duplicate and
nothing is written. A different `--batch` label creates a fresh set of six
orders. Seeding inserts only products that are missing; it never changes
prices or restores stock, so a level only moves when the worker applies an
order. To reset a level, update the `products` table directly.

If the API cannot be reached, or answers with anything other than `201` or
`200`, the command prints the error and exits with status 1. A timeout can
land after an order was accepted, so the recovery is simply to re-run the
same batch: the accepted orders come back as duplicates.

## Walkthrough

One ordered demonstration from an empty database, covering the successful
flow, the duplicate path and the interruption with catch-up. The figures
below assume this sequence and the shipped catalogue, with no other orders.
The example date `2026-10-07` stands for the UTC date on which step 3 runs,
and timestamps in the response bodies are illustrative. Run steps 3 to 9
within one UTC day, or the report in step 9 splits across two dates.

**1. Start from an empty database.** This deletes every order, product and
stock level in the development and test databases:

```bash
docker compose down -v
docker compose up -d --wait postgres
uv run alembic upgrade head
```

**2. Start the API and the worker** in two terminals, as under [Run](#run).
`curl http://127.0.0.1:8000/health` returns `{"status":"ok"}`.

**3. Seed and burst:**

```bash
uv run orders-stock-seed-burst
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

The worker's terminal logs `applied stock for order_id=...` six times, one
per distinct order, oldest first.

**4. Read an order.** Prices were copied onto the items at acceptance and the
stock status has moved from `pending` to `applied`:

```bash
curl -s http://127.0.0.1:8000/orders/web-100045
```

```
{"order_ref":"web-100045","customer_id":"cust-42",
 "items":[{"sku":"BAN-001","qty":2,"unit_price_cents":199},
          {"sku":"APL-003","qty":1,"unit_price_cents":349}],
 "total_cents":747,"status":"accepted","stock_status":"applied",
 "accepted_at":"2026-10-07T09:14:58.003164Z"}
```

**5. Read a stock level.** Bananas were seeded at 500; the six orders take 9:

```bash
curl -s 'http://127.0.0.1:8000/stock?sku=BAN-001'
```

```
{"sku":"BAN-001","name":"Bananas 1kg","stock":491}
```

**6. Read the daily report** for today's UTC date:

```bash
curl -s "http://127.0.0.1:8000/reports/daily?date=$(date -u +%F)"
```

```
{"date":"2026-10-07","total_orders":6,"revenue_cents":18077,
 "units_sold":[{"sku":"APL-003","units":8},{"sku":"BAN-001","units":9},
               {"sku":"BRD-004","units":3},{"sku":"MLK-002","units":3}],
 "current_stock":[{"sku":"APL-003","name":"Apples 1kg","stock":492},
                  {"sku":"BAN-001","name":"Bananas 1kg","stock":491},
                  {"sku":"BRD-004","name":"Bread 700g","stock":197},
                  {"sku":"MLK-002","name":"Milk 2L","stock":297}],
 "generated_at":"2026-10-07T09:15:02.123456Z"}
```

Six orders, not eight: the two repeats were never stored. Revenue is the sum
of the stored totals at the prices in effect when each order was accepted.

**7. Replay the same batch** (the duplicate path):

```bash
uv run orders-stock-seed-burst
```

```
Seeded 4 products (0 new, 4 existing)
web-100045 -> 200 duplicate
web-100046 -> 200 duplicate
web-100045 -> 200 duplicate
web-100047 -> 200 duplicate
web-100048 -> 200 duplicate
web-100049 -> 200 duplicate
web-100046 -> 200 duplicate
web-100050 -> 200 duplicate
Submitted 8 orders: 0 new, 8 duplicates
```

The worker logs nothing, and steps 5 and 6 return the same figures (only
`generated_at` differs): no product was duplicated, no stock level moved and
no `order_ref` was counted twice.

**8. Interrupt the stock capability.** Stop the worker with Ctrl+C in its
terminal; it logs `stock worker stopped` and exits 0. The API keeps
accepting. Submit a fresh batch:

```bash
uv run orders-stock-seed-burst --burst-only --batch demo2
```

Six new orders and two duplicates, as in step 3, but with `demo2-` refs.
`curl -s http://127.0.0.1:8000/orders/demo2-100045` reports
`"stock_status":"pending"`, step 5 still returns `491`, and the report now
counts 12 orders and 36,154 cents while its `current_stock` is unchanged:
sales are recorded at acceptance, stock only when the worker applies them.
The pending work is in PostgreSQL, so it survives any restart.

**9. Recover.** Start the worker again with `uv run orders-stock-worker`. It
logs `applied stock for order_id=...` for each of the six backlog orders,
oldest first. Afterwards `demo2-100045` reports `"stock_status":"applied"`
and the figures are:

| | After step 6 | After step 9 |
|---|---|---|
| `total_orders` (today) | 6 | 12 |
| `revenue_cents` (today) | 18,077 | 36,154 |
| `units_sold` APL-003 / BAN-001 / BRD-004 / MLK-002 | 8 / 9 / 3 / 3 | 16 / 18 / 6 / 6 |
| Stock APL-003 / BAN-001 / BRD-004 / MLK-002 | 492 / 491 / 197 / 297 | 484 / 482 / 194 / 294 |

Submitting any of those `order_ref` values again, at any point, returns `200`
with the existing order and leaves every figure where it is.

## API reference

The interactive documentation at `/docs` lists every route. The examples
here are **not part of the walkthrough**: the `POST` below creates one more
order, adding 2,997 cents to that day's report and taking two more bananas
and one more milk from stock once applied.

### Orders

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
 "accepted_at":"2026-10-07T09:20:40.003164Z"}
```

Send the same request again and the response is `200 OK` with the same
order; nothing is written. `GET /orders/web-100099` returns the same body.
`status` is the acceptance status; `stock_status` is `pending` until the
worker applies the stock decrement, then `applied`. An unknown SKU is
rejected with `422` and `{"detail":"Unknown SKU(s): NOPE-000"}`; an unknown
`order_ref` on `GET` is `404`. An `order_ref` may contain letters, digits,
`.`, `_`, `~` and `-` (up to 128 characters, not only dots) so it can appear
unencoded in the `GET` path. A SKU repeated within one request is merged
into one line; a quantity or total too large for its database column is a
`422` rather than a database error.

### Stock

`GET /stock?sku=BAN-001` returns `{"sku":"BAN-001","name":"Bananas 1kg","stock":491}`,
the live level (491 is the value after walkthrough step 6). An unknown SKU
is `404` with `{"detail":"Unknown SKU"}`; a
missing or empty `sku` is `422`. The SKU is a query parameter rather than a
path segment so that any catalogue SKU, including one containing `/` or
`.`, can be read.

### Daily report

`GET /reports/daily?date=YYYY-MM-DD` summarises one UTC calendar day of
accepted orders, by acceptance time, plus the current stock of every
product (see step 6 for a body). `total_orders` and `revenue_cents` count
stored orders only, so duplicates never appear, and revenue uses the prices
in effect when each order was accepted. `units_sold` lists only SKUs sold
that day. `current_stock` is live, not a per-day snapshot: every product's
level as of `generated_at`, which still lags any accepted orders the worker
has not applied. All figures come from one database snapshot. A day with no
orders returns zeros, an empty `units_sold` and the current stock. A missing
or malformed `date` is `422`.

## Run in containers

Everything can also run in Docker: one image, built from
[Dockerfile](Dockerfile) and the committed `uv.lock`, serves the API, the
worker, the migrations and the seed/burst command. Only Docker is needed on
the host.

```bash
cp .env.example .env                  # the same credentials file as above
docker compose up --build -d --wait   # build the image, then start everything in order
```

Compose starts PostgreSQL, waits for its health check, runs
`alembic upgrade head` in a one-shot `migrate` service, and only then starts
`api` and `worker`. Migrations run in that one job and nowhere else. The API
listens on `0.0.0.0:8000` inside its container and is published on
http://127.0.0.1:8000, so every `curl` above works unchanged.
`--wait` returns once the API's own health check on `/health` passes;
`migrate` shows as `Exited (0)` in `docker compose ps -a`, which is its
finished state.

The containers reach the database at `postgres:5432`, the Compose service
address, through a `DATABASE_URL` that the Compose file assembles from the
`POSTGRES_*` values in `.env`. The `.env` file itself is not passed into the
containers, so the native `localhost:5433` URL is untouched; `LOG_LEVEL` and
`WORKER_POLL_INTERVAL_SECONDS` are forwarded.

The [walkthrough](#walkthrough) runs unchanged in containers with these
commands in place of the native ones. Its output and figures are the same.

| Step | Native | Containers |
|---|---|---|
| 1. Empty database and migrate | `docker compose down -v`, `up -d --wait postgres`, `alembic upgrade head` | `docker compose down -v` then `docker compose up --build -d --wait` |
| 2. Start the API and worker | two `uv run` processes | started by `up`; `docker compose logs -f api worker` follows both |
| 3. Seed and burst | `uv run orders-stock-seed-burst` | `docker compose run --rm seed-burst` |
| 7. Replay | same command again | same command again |
| 8. Stop the worker; fresh batch | Ctrl+C; `uv run orders-stock-seed-burst --burst-only --batch demo2` | `docker compose stop worker`; `docker compose run --rm seed-burst --burst-only --batch demo2` |
| 9. Restart the worker | `uv run orders-stock-worker` | `docker compose start worker` |

`seed-burst` runs on request, never at startup, against the API over the
container network. `run` starts the API and its dependencies if they are not
running and re-runs the `migrate` job, which is a no-op once the schema is
at head. It never starts the worker, so the interruption step is safe.
`docker compose stop worker` delivers SIGTERM to the worker process itself,
because the image runs each command in exec form with no shell in between,
so the graceful shutdown applies exactly as it does natively. The worker
service allows 30 seconds for that, instead of the 10 second Compose
default, because a statement blocked inside PostgreSQL is not interrupted.
If the worker is still inside a transaction after that, Compose kills it:
PostgreSQL rolls the attempt back, the work row stays pending and the
restarted worker retries it, so stock is never applied twice. The API keeps
serving while the worker is down. After a code change,
`docker compose up --build -d --wait` rebuilds the image and recreates the
changed services; `docker compose down` stops and removes the containers.

**Resetting.** Data lives in the named volume `postgres-data` and survives
`stop`, `start`, `restart`, `down` and rebuilds. Neither startup nor seeding
resets anything: the seed adds missing products only and never changes stock.
To start from an empty database, remove the volume explicitly. This deletes
every order, product and stock level in both the development and the test
database:

```bash
docker compose down -v                # deletes this project's database volume
```

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
([ci.yml](.github/workflows/ci.yml)), which also builds the image and
smoke-checks it.

## Project layout

| Path | Purpose |
|---|---|
| `src/orders_stock/api/` | FastAPI application factory, the session dependency and the API entry point |
| `src/orders_stock/orders/` | Orders capability: request/response schemas, the acceptance service and its router |
| `src/orders_stock/stock/` | Stock capability: the worker loop (`worker.py`), the stock router and its schemas |
| `src/orders_stock/worker_main.py` | Stock worker entry point: startup check, signal handling, the polling loop |
| `src/orders_stock/seed_burst.py` | The seed and burst command |
| `src/orders_stock/reports/` | The daily report: router, response schema and the snapshot queries |
| `src/orders_stock/models.py` | SQLAlchemy models for all tables; the schema is described in [SOLUTION.md](SOLUTION.md#schema) |
| `src/orders_stock/config.py`, `db.py`, `logging_config.py` | Settings, database engine/session factory, logging setup |
| `alembic/` | Migrations; `alembic/env.py` takes the URL from settings |
| `Dockerfile`, `.dockerignore` | The application image: one build for the API, worker, migrations and seed/burst |
| `docker-compose.yml`, `docker/` | PostgreSQL with its init script, the one-shot `migrate` job, the `api` and `worker` services and the on-request `seed-burst` service |
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
