"""Bulk uploads.

Revision ID: 0004_batches
Revises: 0003_prompt_version
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_batches"
down_revision: str | None = "0003_prompt_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID = sa.String(40)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "document_batches",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("request_id", sa.String(40), nullable=True),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_document_batches_organization_id_organizations", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_batches"),
    )
    op.create_index(
        "ix_document_batches_organization_id", "document_batches", ["organization_id"]
    )
    op.create_index("ix_document_batches_request_id", "document_batches", ["request_id"])
    op.create_index(
        "ix_document_batches_org_created",
        "document_batches",
        ["organization_id", "created_at"],
    )

    op.add_column("documents", sa.Column("batch_id", ID, nullable=True))
    op.create_index("ix_documents_batch_id", "documents", ["batch_id"])
    op.add_column("extraction_jobs", sa.Column("batch_id", ID, nullable=True))
    op.create_index("ix_extraction_jobs_batch_id", "extraction_jobs", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_extraction_jobs_batch_id", table_name="extraction_jobs")
    op.drop_column("extraction_jobs", "batch_id")
    op.drop_index("ix_documents_batch_id", table_name="documents")
    op.drop_column("documents", "batch_id")
    op.drop_table("document_batches")
