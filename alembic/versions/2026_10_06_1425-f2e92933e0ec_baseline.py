"""Baseline: an empty schema from which all tables are added by later revisions.

Revision ID: f2e92933e0ec
Revises:
Create Date: 2026-10-06 14:25:53.210342
"""

from collections.abc import Sequence

revision: str = "f2e92933e0ec"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
