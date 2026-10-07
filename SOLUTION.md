# Solution

How the orders-and-stock assessment is built, why, and what it leaves out.
[README.md](README.md) covers setup, commands and the walkthrough;
[AGENTS.md](AGENTS.md) records the same decisions as rules for contributors.

## Architecture

Two processes run from one codebase and share nothing but PostgreSQL:

| Process | Responsibility |
|---|---|
| API (`orders-stock-api`) | Accepts orders; serves order details and status, current stock for a SKU, and the daily report |
| Stock worker (`orders-stock-worker`) | Applies persisted pending stock work to product stock levels |

Orders and Stock are separate packages. Accepting an order never calls stock
code: the API writes a pending `stock_work` row in the same transaction as
the order, and the worker applies it later. That row is the contract between
the components. It carries the SKU quantities, so the worker never reads the
Orders tables; the Orders package reads only the row's status, to report it.
Tests enforce both import boundaries and the worker's table footprint.

The daily report (Task 2, Option B) is a third, read-only package over both
components' tables. Neither component imports it.

The brief allows a single process with a background thread. Separate
processes make the interruption demonstration a real stop and restart, with
no demo-only pause switch, and intake continues while the worker is down.

## Accepting an order

`POST /orders` is one transaction:

1. Pydantic validates the request structure before the service runs: at
   least one item, positive quantities, identifier lengths and the
   `order_ref` format. Nothing below executes for a malformed request.
2. Insert the order row with `ON CONFLICT (order_ref) DO NOTHING` and a
   provisional total of zero, before any catalogue validation.
3. If no row comes back, the `order_ref` already exists. Roll back, having
   written nothing, and return the stored order with `200`.
4. Otherwise read the current prices of the requested SKUs. An unknown SKU
   rolls the provisional row back and the response is `422`.
5. Store the total, the items with the unit price in effect, and one pending
   `stock_work` row. Commit and respond `201`.

The unique constraint on `order_ref` is the sole arbiter of duplicates, and
inserting before validating is what makes it reliable under concurrency. A
repeat that arrives while its original is still uncommitted does not slip
into validation as a new order: PostgreSQL makes the competing insert wait
for the original's transaction. If the original commits, the competitor sees
the conflict and is answered as a duplicate. If the original rolls back, for
example on an unknown SKU, the competing insert proceeds and that request
becomes the order. Either way one order exists per `order_ref`. Validating
first would still let a racing repeat past the constraint. The cost is one
`UPDATE` of the total per accepted order and a rolled-back row per rejected
request.

Idempotency is keyed by `order_ref` alone. A structurally invalid repeat is
still a `422`, but a well-formed repeat is neither validated against the
catalogue nor compared with the stored order: it receives the original with
`200`. A `409` was rejected because a repeat is a success, not an error.
Detecting a changed payload would need a comparison rule and a mismatch
response that the brief does not ask for.

Also part of the contract: a SKU repeated in one request is merged into one
line; `status` is always `accepted` for a stored order while `stock_status`
is `pending` until the worker processes the row, then `applied` (the row
itself says `processed`; the API describes the effect on stock); `order_ref`
is limited to URL-unreserved characters so every accepted ref appears
unencoded in `GET /orders/{order_ref}`. The service function owns the
transaction, committing a success and rolling back a conflict or failure;
the request-scoped session dependency manages only the session's lifetime.

## Applying stock

The worker runs one transaction per order:

1. Claim the oldest pending row with `SELECT ... FOR UPDATE SKIP LOCKED`.
2. For each `{sku, qty}` in the row, in sorted SKU order, run
   `UPDATE products SET stock = stock - qty`.
3. Mark the row `processed` and commit.

The decrement and the completion marker commit together, which is the whole
recovery story:

| Interruption | Result |
|---|---|
| Worker is down when an order arrives | Order accepted; its work row waits in PostgreSQL |
| Worker fails after decrementing, before committing | Rollback; the row stays pending and is retried |
| Worker commits, then dies | Stock and marker are both durable; nothing is retried |

`SKIP LOCKED` lets several workers run without claiming the same row. The
atomic `UPDATE` means concurrent decrements of one SKU serialise on the row
lock and none is lost, and the sorted order gives workers applying
overlapping orders a consistent lock order. When nothing is pending, or an
attempt fails, the loop waits `WORKER_POLL_INTERVAL_SECONDS` (default one
second). Retry is minimal: a failed row is logged, stays pending and is
retried, with no attempt counter and no failed state.

SIGINT and SIGTERM set a flag that the loop checks before each attempt and
again once a row is claimed. A claim that finds the flag set is released
unapplied; an order past that point is finished whole, and the process exits
0. The flag cannot interrupt a statement already waiting inside PostgreSQL,
so shutdown waits for it. A worker killed outright leaves an open
transaction for PostgreSQL to roll back: the second row of the table.

## Schema

Four tables, versioned in Alembic and mapped in `src/orders_stock/models.py`:

| Table | Owner | Purpose |
|---|---|---|
| `products` | Stock | SKU (primary key), name, price in cents, stock level |
| `orders` | Orders | `order_ref` (unique), customer, total in cents, acceptance time |
| `order_items` | Orders | SKU, quantity and the unit price in effect at acceptance |
| `stock_work` | Contract | One row per order (unique): status, SKU quantities, timestamps |

Money is integer cents (`integer` for prices, `bigint` for totals) and
timestamps are `timestamptz`. Prices and totals are checked non-negative and
quantities positive. Stock is deliberately unchecked.

- **SKU is the product key.** Everything identifies products by SKU; a
  surrogate id would only add a join.
- **The work payload is JSONB**, an array of `{sku, qty}` objects with one
  entry per SKU: a message the worker consumes whole, with no second table
  mirroring `order_items`. The schema checks only that it is an array;
  Orders builds a valid payload and the worker decrements each entry once.
- **The work row references the order** through a unique foreign key, so it
  can be neither orphaned nor duplicated. A partial index on pending rows
  keeps the oldest-first claim cheap as processed rows accumulate.

## Daily report

`GET /reports/daily?date=YYYY-MM-DD` returns the day's order count and
revenue, units sold per SKU (sold SKUs only), every product's current stock,
and `generated_at`.

A day is a UTC calendar day by `accepted_at`, the half-open interval from
midnight to the next midnight, so an order at exactly midnight belongs to
the new day. The last representable date cannot form that interval and is a
`422`, as is any value not strictly in `YYYY-MM-DD` form.

The three queries run in one read-only `REPEATABLE READ` transaction scoped
to the request, so an order that commits mid-report is counted consistently
or not at all. Orders are counted and summed without joining items, and
units are grouped in a separate query, so a multi-item order cannot multiply
the count or the revenue. Revenue sums stored totals, so it reflects the
prices at acceptance, and duplicates never appear because they are never
stored.

`generated_at` is `now()` inside that transaction, which in PostgreSQL is
the transaction's start time. It is not an exact timestamp of the snapshot,
and `current_stock` is not a historical level for the reported day: it is
every product's live stock as of that transaction, which still lags any
accepted orders the worker has not applied.

## The decisions that matter

**FastAPI with synchronous database access.** FastAPI gives validated
Pydantic request and response models and generated OpenAPI documentation,
most of what an interface other teams can build on needs; Flask would need
extensions for the same and Django brings more than the brief uses.
Endpoints are plain `def` functions with a `Session` and the worker is a
blocking loop. FastAPI runs `def` endpoints on a thread pool, so requests
are still served concurrently, and the concurrency that matters is settled
by PostgreSQL constraints and row locks, not by the Python execution model.
A transaction is one session on one thread, without the lazy-loading and
fixture complications of async code. Throughput is bounded by the thread and
connection pools, which the brief does not stress.

**Shared PostgreSQL with durable work rows.** The transactional outbox,
polled with `SKIP LOCKED`, is the simplest mechanism that gives the
guarantees above: no order without its work, no stock change without its
completion marker, nothing held only in memory. A stock-status column on
the order row would save a table but have the worker writing Orders tables.
`LISTEN/NOTIFY` or a broker would cut latency, but notifications are not
durable on their own and a broker is infrastructure the brief does not need.
The trade-off is eventual consistency: stock lags acceptance by the polling
interval, the backlog and worker availability. The exactly-once effect also
relies on `stock_work` and `products` sharing one database; a separate Stock
database would need an idempotency key on the stock side.

**Integer cents and historical pricing.** Prices and totals are integers in
cents, as in the brief's example data, so arithmetic is exact. Each order
item stores the unit price read at acceptance and each order stores its
total, so a later price change alters neither past orders nor the report.

**Consistent UTC daily reporting.** Every timestamp is `timestamptz` and the
report's day is defined in UTC by acceptance time, so a day means the same
thing in every time zone and the figures are reproducible. Local-time
reporting would need a time zone parameter and daylight-saving rules for no
gain within the brief. Option A, an integration feed, was not chosen because
it brings cursor and retention semantics plus a demonstration consumer; the
report answers the other teams' need with a few aggregate queries over data
the design already produces.

## Routine choices

`orders-stock-seed-burst` seeds the catalogue directly with
`ON CONFLICT DO NOTHING`, since there is no products API, and submits the
burst over HTTP so it exercises the real surface. Refs are deterministic, so
output is reproducible and a rerun is itself the duplicate demonstration.
Requests are sequential; concurrent duplicates are proved by tests. httpx2
is the runtime client because the FastAPI test client is built on it.

SQLAlchemy 2.x (2.0-style) over Psycopg 3 maps the four tables and owns
sessions, and Alembic migrations are the only way the schema changes. The
three concurrency-critical statements, the `ON CONFLICT` insert, the
`SKIP LOCKED` claim and the stock decrement, are written explicitly rather
than left to ORM behaviour, so what the tests prove is visible in the code.
Pydantic API schemas stay separate from the mapped models. uv manages
Python 3.12, the virtual environment and the lockfile; Ruff formats and
lints; mypy runs in strict mode; pydantic-settings reads typed settings from
the environment and a `.env` file. Engines use a short connect timeout so a
process fails fast when PostgreSQL is unreachable.

PostgreSQL 17 runs in Docker Compose on host port 5433 to avoid a locally
installed server. The application runs natively for development and tests,
and the same Compose file can run everything in containers: one image for
all roles (two stages, `uv sync --locked --no-dev`, non-root, exec-form
command so SIGTERM reaches the process), a one-shot `migrate` job that `api`
and `worker` wait for, so migrations run in one place rather than in each
process, and a `seed-burst` service behind a profile that depends on `api`
alone, so running it never restarts a deliberately stopped worker.
Containers get a `DATABASE_URL` built from the `POSTGRES_*` values with the
service address; `.env` itself is not passed through. There is no restart
policy, so a worker that exits on a failed startup check stays down until
started again.

## Testing evidence

Tests run against a dedicated `orders_stock_test` database, migrated to head
by the real Alembic migrations once per session and truncated between tests,
with sessions that commit for real. That is what allows the concurrency and
recovery tests, which need separate connections and visible commits:

- **Duplicates:** concurrent submissions of one `order_ref` yield one order
  and one work row; a repeat with an unknown SKU racing its uncommitted
  original receives the original; a repeat writes nothing.
- **Pricing:** a later price change does not alter a stored total.
- **Recovery:** a failure injected between the decrement and the commit
  leaves stock and the row unchanged; two workers never process the same
  row; concurrent orders for one SKU produce the combined deduction; rows
  created while no worker runs are processed once one starts.
- **Shutdown:** a signal during a transaction finishes it and starts no
  other; a signal after a claim releases the row unapplied.
- **Boundaries:** neither component imports the other; the worker touches
  only `stock_work` and `products`.
- **Schema and report:** the constraints reject what they should, stock may
  go negative, migrations round-trip from empty; the UTC day boundary,
  separate aggregation and the single snapshot hold.

GitHub Actions runs the suite, Ruff and mypy against a PostgreSQL 17 service
container, builds the image and smoke-checks it. The full Compose
demonstration is manual.

## Assumptions

- Seeded stock covers the documented burst and demonstration; it is not
  replenished for unlimited extra batches and re-seeding never resets it.
- Orders are validated for structure, positive quantities and known SKUs;
  business validation beyond that (customer existence, order size limits)
  is out of scope.
- A well-formed repeat of an `order_ref` is the same order; payload
  differences between repeats are not detected.
- Monetary values are integer cents. Report days are UTC calendar days by
  acceptance time.
- No authentication, as the brief permits.

## Limitations

- **Negative stock is allowed.** Acceptance does not check stock, the
  worker always applies the decrement and there is no non-negative
  constraint, so a shortage yields a negative level rather than a
  rejection. The brief limits the unhappy paths to two, and rejecting on
  shortage would add a third order lifecycle. Checking in the worker and
  rejecting on shortage is the natural next step; backorders or reservation
  before confirmation are the heavier alternatives.
- **Stock is eventually consistent.** It lags acceptance by the polling
  interval, the backlog and worker availability, and the report's current
  stock lags the same way.
- **Both processes depend on the shared database.** A worker outage does
  not stop intake; a PostgreSQL outage stops both.
- **A permanently failing work row stalls a single worker.** Claims are
  oldest-first and there is no failed state, so a row that always fails (a
  malformed payload, a decrement out of `integer` range) is reclaimed on
  every poll. With several workers the others skip it only while one holds
  it, so throughput degrades rather than stops. The next step would be an
  attempt counter and a quarantined status.
- **Shutdown can wait on an in-flight database statement.** The stop flag
  is checked between statements, not inside PostgreSQL, so a worker blocked
  on a row lock exits only when that statement returns.
