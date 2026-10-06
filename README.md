# orders-and-stock

Mr D take-home assignment: a minimal orders flow that updates stock, built with
Python and PostgreSQL. The brief is in [docs/Requirements.pdf](docs/Requirements.pdf).

## Status

The repository currently holds the assessment brief and the guidance for
AI-assisted development. There is no application code yet, so there is nothing
to run. Run instructions will be added here once they exist and have been
checked.

The architecture is decided: FastAPI with SQLAlchemy, Psycopg and Alembic, a
separate stock worker process fed by a PostgreSQL pending-work table, and Task
2 Option B (daily report). See [SOLUTION.md](SOLUTION.md) for the design and
[AGENTS.md](AGENTS.md) for the resulting development rules. Python version and
tooling will be chosen during scaffolding.

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

On Windows, enable Developer Mode and run `git config --global core.symlinks true`
before cloning, otherwise the `.claude/skills` symlink is checked out as a text
file.

### Superpowers

Development follows the [Superpowers](https://github.com/obra/superpowers)
workflow.

- Claude Code: run `/plugin install superpowers@claude-plugins-official`.
- Codex: follow the Codex install instructions in the Superpowers README.
