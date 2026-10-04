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

**Agreed decisions**

- FastAPI is the web framework.

**Unresolved**

- Database access library and migration tooling.
- Synchronous or asynchronous database access.
- Worker execution and communication mechanism.
- Task 2 Option A or Option B.
- Python version and development tooling.

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
  request (including a draft), or posting comments on issues or pull requests.
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
- Use the formatter, linter and type checker once they are chosen.

## Persistence and reliability

- Make transaction boundaries and commit ownership explicit.
- Enforce critical invariants in PostgreSQL where appropriate.
- Handle duplicate submissions safely under concurrent requests.
- Preserve historical order pricing when product prices change.
- Persist the work needed for recovery; do not rely solely on memory.
- Ensure retrying stock processing cannot apply an order twice.
- Keep acceptance status and stock-processing status understandable.
- Use versioned schema changes once migration tooling is selected.

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

Add exact development commands here once tooling is established and the
commands have been verified. Do not invent commands in advance. None exist yet.
