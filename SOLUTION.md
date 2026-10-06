# Solution

Design decisions and trade-offs for the orders-and-stock assessment. Each
decision is recorded here when it is made. Choices that are still open are
listed in [AGENTS.md](AGENTS.md).

## Architecture

Two processes run from one codebase and share nothing but PostgreSQL:

| Process | Responsibility |
|---|---|
| API | Accepts orders; exposes order details and status, current stock for a SKU, and the daily report |
| Stock worker | Picks up persisted pending stock work and applies stock decrements |

Orders and Stock are separate modules. The API never calls stock code when
accepting an order; it records a pending stock-work row, and the worker
applies it later. That row is the contract between the components, and it
carries the SKU quantities the worker needs, so the worker never reads the
Orders tables.

## Correctness and recovery

### Accepting an order (one transaction)

1. Look up the `order_ref`. If an order exists, return it: a repeat is not
   validated or compared against the stored order.
2. Validate the SKUs and read their current prices; reject unknown SKUs.
3. `INSERT` the order with `ON CONFLICT (order_ref) DO NOTHING`.
4. If the row was inserted: insert the order items with the unit price in
   effect, store the total, insert one pending stock-work row, commit.
5. If it conflicted (a concurrent duplicate won the race since step 1): roll
   back, having written nothing, and return the existing order.

The unique constraint on `order_ref` is the arbiter for concurrent duplicate
submissions, so two racing requests with the same `order_ref` produce one
order; the lookup in step 1 only makes the sequential repeat cheap and
consistent. A unique constraint on the work row's order id means a repeat can
never enqueue a second stock update. Because order, items and work commit
together, an accepted order can never lack its stock work. Unit prices are
copied onto the items at acceptance, so later price changes do not alter
historical totals.

### Applying stock (one transaction per order)

1. Claim one pending work row with `SELECT ... FOR UPDATE SKIP LOCKED`.
2. Decrement stock for each SKU and quantity in that row.
3. Mark the row processed and commit.

The decrement and the completion marker commit atomically, which gives the
recovery guarantees:

| Interruption | Result |
|---|---|
| Worker is stopped when an order arrives | Order is accepted; its work row stays pending |
| Worker crashes after decrementing, before committing | Transaction rolls back; the row stays pending and is retried |
| Worker commits, then crashes | Stock and completion are both persisted; nothing is retried |

`SKIP LOCKED` lets several workers run without claiming the same row. One
order per transaction isolates a rollback to that order; it does not remove
contention when orders touch the same stock rows.

### Status

An order is `accepted` once its transaction commits. Its stock status is
`pending` until the worker processes the row, then `applied`. The order
details endpoint reports both, so "received" and "stock reflects it" are
distinguishable.

### The two unhappy paths

- **Duplicates:** the seed/burst command repeats some `order_ref` values.
  Afterwards one order exists per `order_ref` and stock was decremented once
  per order.
- **Interruption and catch-up:** stop the worker process while PostgreSQL
  and the API keep running, submit orders, observe them accepted with stock
  `pending`, restart the worker, observe stock catch up. A real restart
  shows the pending work survives in PostgreSQL, not in memory.

## Decisions

### Web framework: FastAPI

- **Rationale:** FastAPI integrates Pydantic request and response models
  with validation and generated OpenAPI documentation. These features
  directly support clear, documented interfaces that other teams can
  build on, with limited additional integration work.

- **Alternatives:** Flask is a viable lightweight option, but equivalent
  schema validation and OpenAPI support would require extensions or
  additional implementation. Django offers useful integrated database
  tooling and application conventions, although its admin functionality
  is outside the brief. FastAPI was selected for its API-focused features
  and flexibility in choosing persistence and worker tooling.

- **Trade-offs:** Database access, migrations and durable background
  processing must be selected and integrated separately. This provides
  flexibility but leaves more architectural decisions to the application.

### Database access: SQLAlchemy 2.x, Psycopg 3 and Alembic

SQLAlchemy 2.x (the 2.0-style API; 2.1 at the time of writing) for
persistence, Psycopg 3 as the driver and
Alembic for versioned migrations. ORM-mapped models define tables and handle
ordinary reads and writes; the concurrency-critical statements are written
explicitly as Core or SQL: the `ON CONFLICT` insert, the `SKIP LOCKED` claim
and the stock decrement. Pydantic API schemas stay separate from database
models.

- **Rationale:** Alembic reproduces the schema from an empty database.
  Sessions give one place to own transactions. Explicit critical statements
  keep the behaviour under test visible in the code.
- **Alternatives:** Psycopg alone with parameterised SQL is credible for
  this few queries; its row factories handle mapping, and migrations could
  be plain SQL files applied by a small script or by Alembic anyway. It was
  not chosen because the session and migration structure is worth having
  as the schema grows. SQLModel reduces model duplication, but API contracts
  and database records have different responsibilities here and the
  separation is manageable at this size.
- **Trade-offs:** SQLAlchemy's session lifecycle, flushing and loading
  behaviour must be understood and tested.

### Execution model: synchronous

Plain `def` endpoints with a `Session`, and a blocking worker loop.

- **Rationale:** FastAPI runs `def` endpoints on a thread pool, so requests
  are served concurrently. The concurrency that matters here is settled in
  PostgreSQL by constraints and row locks, not by the Python execution
  model. A transaction is one session on one thread, with no lazy-loading
  pitfalls in async code and no async test fixtures.
- **Alternatives:** `AsyncSession` may improve throughput under many slow
  concurrent connections, which the brief does not call for, at the cost of
  async fixtures and stricter loading rules.
- **Limitation:** throughput is bounded by the thread pool and connection
  pool. Moving to async later touches session and endpoint code, not the
  schema or design.

### API and worker: separate processes

- **Rationale:** the worker can be stopped, restarted and diagnosed without
  interrupting intake, the interruption demonstration is a real restart,
  and the component boundary is visible in both code and demo without
  demo-only pause code.
- **Alternatives:** a background thread inside the API process, which the
  brief permits. It starts with one command but needs a pause mechanism for
  the demonstration, and stopping the process to show recovery would also
  stop intake.
- **Trade-offs:** two processes to start and document, plus worker shutdown
  handling. Schema and work-row contract changes must stay compatible
  across both processes.

### Durable processing: transactional pending-work table with polling

The transactional outbox pattern, as described under Correctness and
recovery.

- **Rationale:** PostgreSQL's all-or-nothing transaction makes the
  guarantees simple: no order without its work, no stock change without its
  completion marker. Polling with `SKIP LOCKED` is minimal and the brief
  explicitly allows it.
- **Alternatives:** a stock-status column on the order row saves a table but
  has the worker writing Orders tables. `LISTEN/NOTIFY` or a broker lowers
  latency, but notifications are not durable alone and a broker is extra
  infrastructure.
- **Trade-offs:** stock is eventually consistent; latency depends on the
  polling interval, backlog and worker availability. The exactly-once effect
  relies on the stock and work tables sharing one database; a separate
  Stock database would need an idempotency key on the stock side instead.

### Schema

Four tables, created by one Alembic revision after the baseline, mapped in
`src/orders_stock/models.py`:

| Table | Owner | Purpose |
|---|---|---|
| `products` | Stock | SKU (primary key), name, price in cents, stock level |
| `orders` | Orders | `order_ref` (unique), customer, total in cents, acceptance time |
| `order_items` | Orders | SKU, quantity and the unit price in effect at acceptance |
| `stock_work` | Contract | One row per order (unique): status, SKU quantities, timestamps |

Money is integer cents (`integer` for prices, `bigint` for totals) and
timestamps are `timestamptz`. Prices and totals are checked non-negative and
quantities positive; stock is deliberately unchecked (see Insufficient stock).
Constraint names follow a naming convention on the declarative base so later
migrations and tests can refer to them.

- **SKU as the product key.** The brief, the API, the worker and the report
  all identify products by SKU, so a surrogate id would only add a join.
- **Work payload as JSONB.** `stock_work.items` is a JSON array of
  `{"sku", "qty"}` objects. The work row is a message the worker consumes
  whole: one insert at acceptance, one claim in the worker, and no second
  table mirroring `order_items`. The schema guarantees only that the value
  is an array. Orders builds a valid payload at acceptance with one entry
  per SKU, summing the quantities of any SKU repeated in the request, and the
  worker decrements each entry once.
  Alternative: child rows with a foreign key to `products`, which would
  validate each SKU in the database and let the worker decrement in one
  joined update. Not chosen because the API already validates SKUs against
  `products` and nothing deletes products.
- **Work row references the order.** `stock_work.order_id` is a unique
  foreign key to `orders.id`, so a work row can neither be orphaned nor
  duplicated. This ties the two capabilities to one database, which the
  design already assumes (see Durable processing). A free-standing
  `order_ref` column would ease a later split into separate databases.
- **Status as text with a check.** `pending` or `processed`, plus a check
  that `processed_at` is set exactly when the status is `processed`. A
  partial index on pending rows by `created_at` keeps the worker's
  oldest-first claim cheap as processed rows accumulate.
- **One models module.** Four tables do not need per-component model files.
  The component boundary is which tables each component reads and writes,
  not where the classes are defined, and placing classes in separate modules
  would not enforce that boundary by itself.

### Insufficient stock: out of scope

The API accepts without checking stock, the worker always applies the
decrement, and there is no non-negative constraint on stock.

- **Rationale:** the brief requires that acceptance does not depend on the
  stock capability being available, so this design does not verify stock at
  acceptance. The brief also limits the unhappy paths to two. Rejecting on shortage would add a third lifecycle (a failed
  state, what happens to the order, how the report counts it).
- **Alternatives:** confirm in the worker and reject on shortage, which fits
  the current boundaries and is the natural next step for a real product;
  backorders; or reservation before confirmation.
- **Behaviour if the assumption fails:** a negative stock level, not an
  error. Acceptance confirms persistence; it does not reserve inventory.

### Orders API

`POST /orders` accepts `{order_ref, customer_id, items: [{sku, qty}]}` and
`GET /orders/{order_ref}` returns the order. Both return the same body:
`order_ref`, `customer_id`, `items` (each with `sku`, `qty` and the
`unit_price_cents` snapshot), `total_cents`, `accepted_at`, and two separate
status fields, `status` and `stock_status`.

- **201 for a new order, 200 for a repeat.** A repeated `order_ref` returns
  the existing order with 200 and writes nothing, so the call is idempotent
  from the client's side and the seed/burst command can count new and
  duplicate outcomes by status code. A 409 was rejected because a repeat is
  a success, not an error. "The same body" means the stored order details;
  `stock_status` may legitimately have moved from `pending` to `applied`
  between two submissions.
- **Idempotency is keyed by `order_ref` alone.** The repeat's payload is not
  validated or compared, so a repeat with different items, or with an
  unknown SKU, still returns the original order. Detecting a changed payload
  would need a comparison rule and a response for the mismatch, which the
  brief does not ask for.
- **Two status fields.** `status` is always `accepted` for a stored order
  (an order that is not accepted does not exist). `stock_status` is derived
  from the work row: `pending` until the worker processes it, then
  `applied`. The database stores `processed` on the work row; the API says
  `applied` because it describes the effect on stock, not the worker's
  bookkeeping.
- **Unknown SKU is 422.** The body is `{"detail": "Unknown SKU(s): A, B"}`,
  a plain string rather than Pydantic's list of field errors, because the
  failure concerns the request as a whole against the catalogue. Nothing is
  written. Structural errors (no items, `qty` below 1, blank strings) are
  Pydantic's usual 422.
- **Repeated SKUs in one request are merged** into one order item and one
  work-payload entry with the summed quantity, so the stored order has one
  line per SKU and the worker decrements each SKU once.
- **Range checks.** `qty` is bounded by PostgreSQL's `integer` in the request
  schema, and the merged quantities and total are checked against `integer`
  and `bigint` before insert, so an oversized order is a 422 rather than a
  database error.
- **Wiring.** The application lifespan creates the engine and session
  factory and disposes the engine on shutdown. A dependency yields one
  `Session` per request and owns only its lifetime; the service function
  owns commit and rollback, so the transaction boundary is visible in one
  place. The Orders module reads the work row's status but imports nothing
  from the Stock module.

### Task 2: Option B, daily report

One endpoint returning, for a calendar day, total orders, revenue, units sold
per SKU and current stock per SKU.

- **Rationale:** a few aggregate queries behind one endpoint, no new
  component, deterministic against seeded data. The brief states this option
  satisfies other teams' need to learn about accepted orders.
- **Alternatives:** Option A introduces a consumer-facing feed with cursor
  and retention semantics, plus a demonstration consumer. Option B provides
  the required external visibility through aggregate queries over data the
  design already produces.

### Development tooling

- **uv and Python 3.12.** uv manages the interpreter, virtual environment
  and lockfile, so every command has one form, `uv run ...`, on Linux, macOS
  and Windows, and CI installs it with a first-party action. Python 3.12 is
  widely available and every dependency ships wheels for it; 3.13 or 3.14
  would add nothing for this brief. Alternatives: `pip` with `venv` and a
  requirements file (no lockfile without extra tooling), or Poetry (heavier
  for the same result). Cost: a reviewer needs one install step for uv.
- **Ruff and mypy strict.** Ruff formats and lints with one configuration
  block. mypy runs in strict mode on the application and tests; SQLAlchemy
  2.x and Pydantic are typed natively so no plugins beyond `pydantic.mypy`
  are needed. Pyright would also serve; mypy is the conventional CI choice
  and needs no Node runtime.
- **PostgreSQL 17 in Docker Compose, application native.** One compose
  service with an init script that creates the development and test
  databases. Credentials are not hard-coded in the compose file; it reads
  them from the gitignored `.env`, with local-only values in `.env.example`,
  and refuses to start if they are missing. The application runs natively so
  the worker interruption demo
  is a plain Ctrl+C. The container publishes host port 5433 to avoid
  clashing with a locally installed PostgreSQL. Reviewers without Docker can
  point `.env` at any PostgreSQL 17. Containerising the application itself
  is deferred: configuration is environment-only and there are two entry
  points, so adding a Dockerfile later is cheap, and it would be an extra way
  to run the project rather than a replacement for the native path.
- **pydantic-settings.** Typed settings from environment variables with a
  `.env` file for development and a committed `.env.example`. A missing or
  malformed database URL fails at startup with a clear error. The
  alternative, hand-written `os.environ` reads, duplicates the parsing and
  validation pydantic-settings provides.
- **Test database strategy.** A dedicated `orders_stock_test` database,
  migrated to head once per session by running the Alembic migrations (so
  the real setup path is exercised), truncated between tests, with sessions
  that commit for real. This is what makes the concurrency tests possible:
  concurrent duplicate submissions, `SKIP LOCKED` claims and failures
  injected mid-transaction all need separate connections and visible
  commits. Tests fail rather than skip when the database is unreachable.
  Alternatives: per-test transaction rollback on one connection (rules out
  the concurrency tests), a database per test (slow for the same guarantee)
  or Testcontainers (self-contained, but adds a Docker requirement inside
  the test run when the compose database and CI service container already
  exist).
- **Entry points.** `orders-stock-api` and `orders-stock-worker` console
  scripts, with `python -m` equivalents for environments that block script
  launchers. `fastapi dev` also serves the app via the `[tool.fastapi]`
  entrypoint for auto-reload during development. Engines use a 5 second
  connect timeout so a process fails fast when PostgreSQL is unreachable.

## Assumptions

- Stock is sufficient for the demonstrated workload; seed data supports the
  full burst.
- Orders are validated for structure, positive quantities and known SKUs;
  business validation beyond that (customer existence, order size limits)
  is out of scope.
- A repeated `order_ref` is the same order. Payload differences between
  repeats are not detected.
- Monetary values are integer cents, as in the brief's example data.
- Report days are calendar days in UTC by acceptance time. Revenue is the
  sum of accepted order totals; duplicates are excluded because they are
  never stored. Current stock per SKU is read live, not a per-day snapshot.
- No authentication, as the brief permits.

## Known limitations

- Stock lags acceptance; see Durable processing.
- Both processes depend on one PostgreSQL database. A worker crash does not
  stop intake, but a database outage stops both.
- Insufficient stock yields a negative level rather than a rejection.
