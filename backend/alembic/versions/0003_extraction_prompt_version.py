"""Record which prompt version produced each extraction.

Revision ID: 0003_prompt_version
Revises: 0002_jobs_webhooks
Create Date: 2026-09-18

A prompt change is a behaviour change. Without this column, a shift in
accuracy cannot be attributed to the prompt that caused it (§33).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_prompt_version"
down_revision: str | None = "0002_jobs_webhooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extractions", sa.Column("prompt_version", sa.String(60), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("extractions", "prompt_version")
