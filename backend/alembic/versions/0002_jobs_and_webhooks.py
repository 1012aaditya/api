"""Asynchronous extraction jobs and webhook delivery.

Revision ID: 0002_jobs_webhooks
Revises: 0001_initial
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_jobs_webhooks"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
ID = sa.String(40)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "extraction_jobs",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("document_id", ID, nullable=False),
        sa.Column("api_key_id", ID, nullable=True),
        sa.Column("extraction_id", ID, nullable=True),
        sa.Column("request_id", sa.String(40), nullable=True),
        sa.Column("document_type", sa.String(60), nullable=False, server_default="gst_invoice"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", TS, nullable=False),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", TS, nullable=True),
        sa.Column("completed_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_extraction_jobs_organization_id_organizations", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"],
            name="fk_extraction_jobs_document_id_documents", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["api_keys.id"],
            name="fk_extraction_jobs_api_key_id_api_keys", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_jobs"),
    )
    op.create_index("ix_extraction_jobs_organization_id", "extraction_jobs", ["organization_id"])
    op.create_index("ix_extraction_jobs_document_id", "extraction_jobs", ["document_id"])
    op.create_index("ix_extraction_jobs_request_id", "extraction_jobs", ["request_id"])
    op.create_index("ix_extraction_jobs_available_at", "extraction_jobs", ["available_at"])
    op.create_index(
        "ix_extraction_jobs_org_created", "extraction_jobs", ["organization_id", "created_at"]
    )
    op.create_index("ix_extraction_jobs_claim", "extraction_jobs", ["status", "available_at"])

    op.create_table(
        "webhooks",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
        sa.Column("events", JSON_TYPE, nullable=False),
        sa.Column("secret_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disabled_at", TS, nullable=True),
        sa.Column("last_delivery_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_webhooks_organization_id_organizations", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_webhooks"),
    )
    op.create_index("ix_webhooks_organization_id", "webhooks", ["organization_id"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("webhook_id", ID, nullable=False),
        sa.Column("event", sa.String(60), nullable=False),
        sa.Column("job_id", ID, nullable=True),
        sa.Column("document_id", ID, nullable=True),
        sa.Column("payload", JSON_TYPE, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", TS, nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("delivered_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_webhook_deliveries_organization_id_organizations", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["webhook_id"], ["webhooks.id"],
            name="fk_webhook_deliveries_webhook_id_webhooks", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_deliveries"),
    )
    op.create_index(
        "ix_webhook_deliveries_organization_id", "webhook_deliveries", ["organization_id"]
    )
    op.create_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"])
    op.create_index(
        "ix_webhook_deliveries_next_attempt_at", "webhook_deliveries", ["next_attempt_at"]
    )
    op.create_index(
        "ix_webhook_deliveries_pending", "webhook_deliveries", ["status", "next_attempt_at"]
    )
    op.create_index(
        "ix_webhook_deliveries_org_created",
        "webhook_deliveries",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
    op.drop_table("extraction_jobs")
