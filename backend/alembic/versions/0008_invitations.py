"""Invitations, so a firm is more than one login.

Revision ID: 0008_invitations
Revises: 0007_ai_planning
Create Date: 2026-09-20

A CA firm is three to twenty people sharing one client list. Until now the
person who signed up was the only one who could get in, which meant either
the whole office shared one password or only one person could use the thing.

There is no mail server here, so an invitation is a link the firm sends
however they already talk. That makes the token a credential: stored hashed,
shown once, single use, and it expires.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_invitations"
down_revision = "0007_ai_planning"
branch_labels = None
depends_on = None

ID = sa.String(40)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", ID, primary_key=True),
        sa.Column(
            "organization_id",
            ID,
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        # No server default: the model does not declare one, and a schema
        # that quietly disagrees with the models is how a migration stops
        # being a record of what the database is.
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "invited_by_user_id",
            ID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("accepted_at", TS, nullable=True),
        sa.Column(
            "accepted_user_id",
            ID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_invitations_organization_id", "invitations", ["organization_id"])
    op.create_index("ix_invitations_email", "invitations", ["email"])
    op.create_index(
        "ix_invitations_token_hash", "invitations", ["token_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_invitations_token_hash", table_name="invitations")
    op.drop_index("ix_invitations_email", table_name="invitations")
    op.drop_index("ix_invitations_organization_id", table_name="invitations")
    op.drop_table("invitations")
