# Orders and Stock

Shared instructions for AI coding agents working in this repository.

## Project context

Build the Python and PostgreSQL orders-and-stock assessment described in
`docs/Requirements.pdf`. The brief is the source of requirements.

Read the brief and the relevant repository files before making changes.
Distinguish requirements from implementation choices. Flag conflicting or
ambiguous requirements rather than silently interpreting them as new scope.

## Scope

The system must:

- Accept and persist orders.
- Prevent duplicate submissions with the same `order_ref` from being counted
  twice.
- Calculate totals using product prices at the time of the order.
- Expose order creation and order details/status.
- Persist products and stock, and expose current stock for a SKU.
- Accept orders during a brief stock-capability interruption and apply the
  stock updates after recovery.
- Keep the Orders and Stock capabilities as components that can run and evolve
  independently. A single process with a background worker is acceptable.
- Provide a small seed/burst command that includes duplicate orders.
- Implement exactly one Task 2 option: an integration surface or a daily
  report.
- Demonstrate exactly two representative unhappy paths: duplicates, and
  interruption followed by catch-up.

Deliver a runnable repository, `README.md`, `SOLUTION.md` and a 5–10 minute
video demonstration. The application must run locally on Linux or macOS.

Keep additional business edge cases outside the assessment scope. Document
assumptions and limitations.

## Decisions

Keep these three groups separate. Do not treat a proposal as a decision.

**Assessment requirements** (fixed by the brief)

- Python and PostgreSQL.
- Data persisted in PostgreSQL, not held only in memory.

**Agreed decisions** (rationale in `SOLUTION.md`)

- FastAPI is the web framework.
- SQLAlchemy 2.0 with Psycopg 3 for database access; Alembic for migrations.
  Pydantic API schemas stay separate from database models.
- Synchronous database access: `def` endpoints with a `Session`; the worker
  is a blocking loop.
- The API and the stock worker are separate processes from one codebase,
  communicating only through PostgreSQL.
- Order acceptance writes a pending stock-work row in the same transaction
  as the order. The worker claims rows with `FOR UPDATE SKIP LOCKED` and
  applies the stock decrements and the completion marker in one transaction
  per order.
- Insufficient stock is out of scope: no stock check at acceptance, no
  non-negative constraint, stock may go negative.
- Orders API: `POST /orders` returns 201 for a new order and 200 with the
  existing order for a repeated `order_ref`; idempotency is by `order_ref`
  alone and a well-formed repeat's payload is neither validated against the
  catalogue nor compared. `GET /orders/{order_ref}`
  reports `status` (`accepted`) and `stock_status` (`pending`/`applied`)
  as separate fields. Unknown SKU is 422 with a string `detail`; unknown
  order is 404. `order_ref` is restricted to URL-unreserved characters
  (letters, digits, `.`, `_`, `~`, `-`), at most 128 long. Repeated SKUs in one request are merged into one line. The
  service function owns commit and rollback; the request-scoped session
  dependency owns only the session's lifetime.
- Task 2 Option B: the daily report.
- Schema: products are keyed by SKU; money is integer cents and timestamps
  are `timestamptz`. The stock-work row has a unique foreign key to the
  order and carries the SKU quantities as a JSONB array of `{sku, qty}`
  objects with one entry per SKU; the schema checks only that it is an
  array, Orders builds the payload (summing repeated SKUs) and the worker
  decrements each entry once. All mapped models live in
  `src/orders_stock/models.py`; constraint names follow the naming
  convention on `Base`.
- uv with Python 3.12; Ruff for formatting and linting; mypy in strict mode;
  pytest. PostgreSQL 17 runs in Docker Compose on host port 5433; the
  application runs natively. Settings come from environment variables via
  pydantic-settings. Tests use the dedicated `orders_stock_test` database,
  migrated with Alembic once per session and truncated between tests, with
  real commits.

**Unresolved**

- None at present.

Resolve the decisions needed for the current issue, move them to "Agreed
decisions" in the same change, and record their rationale in `SOLUTION.md`. Do
not silently adopt a skill's preferred stack as a project decision.

## Issue-by-issue workflow

- Read the issue and its acceptance criteria before implementation.
- Inspect existing code and conventions before proposing changes.
- Keep each change focused on the current issue.
- Include relevant tests and documentation with the implementation.
- Raise consequential design questions before committing to an approach.
- Complete routine implementation choices without unnecessary pauses.
- Document significant design decisions in SOLUTION.md, including their rationale, relevant alternatives and limitations. Keep the detail proportional to the decision.
- Report completed work, verification results and remaining limitations.
- Do not begin unrelated work unless requested.

## Issue size and reviewability

- Assess issue scope before implementation.
- If an issue contains several independently meaningful outcomes, or its
  changes would be difficult to review together, propose sub-issues first.
- Give each sub-issue a clear outcome, acceptance criteria, dependencies and
  verification expectations.
- Keep the parent issue as the overall acceptance checklist.
- Prefer cohesive changes that include their relevant tests and documentation.
  Do not split mechanically by file count, line count or technical layer.
- If scope grows during implementation, revisit the breakdown before
  continuing to expand the change.

## Publishing and GitHub permissions

- Obtain explicit user permission before pushing commits, creating a pull
  request (including a draft), creating or editing issues, or posting
  comments on issues or pull requests.
- Local commits do not need permission. Committing locally does not grant
  permission to publish.
- Permission applies only to the action and scope authorised. Do not infer
  permission for later pushes, pull requests or comments.
- Prepare the changes, verification results and any proposed publication text
  before requesting permission.
- Do not merge pull requests or close issues without explicit permission.
- Workflow skills and commands must respect these restrictions.

## Skills

Project skills live in `.agents/skills/`; their sources and review notes are in
`.agents/skills/SOURCES.md`. Use them when relevant:

- `fastapi`
- `supabase-postgres-best-practices`

Use the installed Superpowers plugin for planning, debugging, testing and
review. Keep the process proportional to the task.

Assessment requirements and recorded project decisions take precedence over
generic skill recommendations. In particular:

- Do not introduce SQLModel, async database access or additional
  infrastructure solely because a skill recommends it.
- Treat example code as a reference, not a complete implementation.
- Check guidance against the project's pinned dependency versions.
- Preserve attribution and licences for vendored skills.

## Python and API conventions

- Prefer readable, idiomatic Python and focused modules.
- Use type annotations for public interfaces.
- Keep HTTP handling, business logic and persistence responsibilities clear
  without introducing unnecessary layers.
- Add abstractions only when they solve a concrete problem.
- Define explicit request and response schemas.
- Catch specific exceptions and preserve useful diagnostics.
- Keep configuration outside application logic and secrets out of Git.
- Use timezone-aware timestamps and exact monetary representations.
- Run Ruff and mypy before considering a change complete; both must be clean.
- Keep `.agents/` and `.claude/` out of formatting and linting; they hold
  vendored content.

## Architecture rules

- Keep Orders and Stock in separate modules. The API must not call stock
  code when accepting an order; it records pending stock work for the worker.
- The pending-work table is the contract between the components. It carries
  the SKU quantities the worker needs; the worker does not read Orders
  tables. Change it deliberately and update both sides together.
- Keep API request and response schemas (Pydantic) separate from SQLAlchemy
  models.
- Write the concurrency-critical statements explicitly rather than relying
  on ORM behaviour: the idempotent order insert (`ON CONFLICT`), claiming
  pending work (`FOR UPDATE SKIP LOCKED`) and the stock decrement.
- Do not add stock checks, reservations, or failure states for insufficient
  stock. Record the assumption instead.

## Persistence and reliability

- Make transaction boundaries and commit ownership explicit. Order
  acceptance is one transaction; applying stock for one order is one
  transaction.
- Enforce critical invariants in PostgreSQL: unique `order_ref`, unique
  work record per order.
- Handle duplicate submissions safely under concurrent requests; the unique
  constraint, not application checks, is the arbiter.
- Preserve historical order pricing when product prices change by storing
  unit prices on order items.
- Persist the work needed for recovery; do not rely solely on memory.
- Ensure retrying stock processing cannot apply an order twice: the
  decrement and the completion marker commit together.
- Report order acceptance status and stock-processing status separately.
- Use versioned Alembic migrations for all schema changes.

## Testing

- Test observable behaviour and important failure boundaries.
- Use unit tests for isolated logic and PostgreSQL integration tests for
  constraints, transactions, concurrency and recovery.
- Do not mock away the database behaviour a test is meant to prove.
- Cover duplicate submissions and interruption/catch-up alongside the
  successful order flow.
- Keep tests isolated and deterministic.
- Prioritise meaningful assertions over arbitrary coverage targets.
- Run checks relevant to the change and report their actual results.
- Never claim a check passed if it was not run.

## Documentation and commands

`README.md` should explain how to set up, run, seed, test and demonstrate the
application.

`SOLUTION.md` should explain the design, trade-offs, assumptions and known
limitations.

### Commands

All commands run from the repository root. Each was run successfully on Linux
(Debian container) and, in its `python -m` form where noted, on Windows.

| Purpose | Command |
|---|---|
| Install dependencies | `uv sync` |
| Start PostgreSQL | `docker compose up -d --wait` |
| Apply migrations | `uv run alembic upgrade head` |
| New migration | `uv run alembic revision -m "<description>"` (use `--autogenerate` once models exist) |
| Run the API | `uv run orders-stock-api` (or `uv run python -m orders_stock.api.main`) |
| Run the worker | `uv run orders-stock-worker` (or `uv run python -m orders_stock.worker_main`) |
| Tests | `uv run pytest` |
| Format check / fix | `uv run ruff format --check .` / `uv run ruff format .` |
| Lint / fix | `uv run ruff check .` / `uv run ruff check --fix .` |
| Type check | `uv run mypy` |

Tests need the PostgreSQL container and a `.env` (copy `.env.example`). Add
dependencies with `uv add <package>` (or `uv add --dev <package>`) so
`uv.lock` stays current; commit the lockfile. CI
(`.github/workflows/ci.yml`) runs the same commands with `uv sync --locked`,
so a stale lockfile fails the build. When a command here changes, change the
workflow in the same pull request.
