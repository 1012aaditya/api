"""Let a firm turn the agent's planner off.

Revision ID: 0007_ai_planning
Revises: 0006_ca_operations
Create Date: 2026-09-19

A model deciding what to do next about a client is a bigger step than a model
reading an invoice, so it is a switch a firm owns rather than a deployment
setting. Default on: with no model configured the planner never runs anyway,
and a firm that has configured one has asked for it.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_ai_planning"
down_revision = "0006_ca_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_policies",
        sa.Column(
            "allow_ai_planning",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_policies", "allow_ai_planning")
