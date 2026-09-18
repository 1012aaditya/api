"""Tally integration: ledger master, learned aliases, posting settings.

Revision ID: 0005_tally
Revises: 0004_batches
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_tally"
down_revision: str | None = "0004_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.String(40)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "ledgers",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("parent_group", sa.String(200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ledgers_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ledgers"),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_ledgers_organization_id_name"
        ),
    )
    op.create_index("ix_ledgers_organization_id", "ledgers", ["organization_id"])
    op.create_index("ix_ledgers_org_gstin", "ledgers", ["organization_id", "gstin"])
    op.create_index(
        "ix_ledgers_org_normalized", "ledgers", ["organization_id", "normalized_name"]
    )

    op.create_table(
        "ledger_aliases",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("ledger_id", ID, nullable=False),
        sa.Column("key_type", sa.String(20), nullable=False),
        sa.Column("match_key", sa.String(300), nullable=False),
        sa.Column("confirmed_by_user_id", ID, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ledger_aliases_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ledger_id"],
            ["ledgers.id"],
            name="fk_ledger_aliases_ledger_id_ledgers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"],
            ["users.id"],
            name="fk_ledger_aliases_confirmed_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_aliases"),
        sa.UniqueConstraint(
            "organization_id",
            "key_type",
            "match_key",
            name="uq_ledger_aliases_organization_id_key_type",
        ),
    )
    op.create_index(
        "ix_ledger_aliases_organization_id", "ledger_aliases", ["organization_id"]
    )
    op.create_index("ix_ledger_aliases_ledger_id", "ledger_aliases", ["ledger_id"])

    op.create_table(
        "tally_settings",
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("company_name", sa.String(300), nullable=True),
        sa.Column(
            "voucher_type", sa.String(100), nullable=False, server_default="Purchase"
        ),
        sa.Column("purchase_ledger", sa.String(300), nullable=True),
        sa.Column("cgst_ledger", sa.String(300), nullable=True),
        sa.Column("sgst_ledger", sa.String(300), nullable=True),
        sa.Column("igst_ledger", sa.String(300), nullable=True),
        sa.Column("utgst_ledger", sa.String(300), nullable=True),
        sa.Column("cess_ledger", sa.String(300), nullable=True),
        sa.Column("round_off_ledger", sa.String(300), nullable=True),
        sa.Column("other_charges_ledger", sa.String(300), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_tally_settings_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_id", name="pk_tally_settings"),
    )


def downgrade() -> None:
    op.drop_table("tally_settings")
    op.drop_index("ix_ledger_aliases_ledger_id", table_name="ledger_aliases")
    op.drop_index("ix_ledger_aliases_organization_id", table_name="ledger_aliases")
    op.drop_table("ledger_aliases")
    op.drop_index("ix_ledgers_org_normalized", table_name="ledgers")
    op.drop_index("ix_ledgers_org_gstin", table_name="ledgers")
    op.drop_index("ix_ledgers_organization_id", table_name="ledgers")
    op.drop_table("ledgers")
