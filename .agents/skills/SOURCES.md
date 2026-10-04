# Skill sources

`.agents/skills/` is the single maintained copy of the project skills.
`.claude/skills` is a symlink to this directory.

Each skill directory is an unmodified copy of the upstream directory at the
revision below, plus the upstream repository's `LICENSE` file.

| Skill | Upstream | Path | Revision | Licence |
|---|---|---|---|---|
| `fastapi` | https://github.com/fastapi/fastapi | `fastapi/.agents/skills/fastapi` | `5f9fc5c59a9bb54608aa35376715f3ba9708188e` (2026-10-02) | MIT |
| `supabase-postgres-best-practices` | https://github.com/supabase/agent-skills | `skills/supabase-postgres-best-practices` | `c9be0e931b7930f7d02126d04774d904c381e7d7` (2026-10-02) | MIT |

To update a skill, replace its directory with the upstream directory at a newer
revision, keep the `LICENSE` file, and update the revision here.

## Review notes

Both skills were reviewed on 2026-10-03 for relevance and for instructions that
conflict with `AGENTS.md` or Superpowers.

- `fastapi`: relevant throughout. It recommends uv, Ruff, ty, SQLModel, Asyncer
  and HTTPX. Those are suggestions, not project decisions; `AGENTS.md` takes
  precedence and lists what is still unresolved. The frontend, streaming and
  OpenTelemetry sections are outside the assessment scope.
- `supabase-postgres-best-practices`: the schema, locking, upsert and query
  rules apply to plain PostgreSQL. The Supabase-specific notes, row-level
  security rules and connection-pooler advice do not apply here (no auth, local
  PostgreSQL).

## Considered and not included

- `python-testing-patterns` (wshobson/agents): generic pytest material that
  whose database examples use mocks and in-memory SQLite in place of a real
  database, where this project tests database behaviour against PostgreSQL. It
  also overlaps with Superpowers test-driven development.
- `design-postgres-tables` (timescale/pg-aiguide): overlaps with the Supabase
  skill; excluded by the repository owner after a separate review reported an
  accuracy problem.
- Skills for uv, Ruff, background jobs and migrations: they would pre-empt
  choices that are still unresolved.
