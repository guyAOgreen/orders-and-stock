# orders-and-stock

Mr D take-home assignment: a minimal orders flow that updates stock, built with
Python and PostgreSQL. The brief is in [docs/Requirements.pdf](docs/Requirements.pdf).

## Status

The repository currently holds the assessment brief and the guidance for
AI-assisted development. There is no application code yet, so there is nothing
to run. Run instructions will be added here once they exist and have been
checked.

FastAPI is the chosen web framework. Database tooling, the worker arrangement
and the choice between Task 2 Option A and Option B are still open; see
[AGENTS.md](AGENTS.md).

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
