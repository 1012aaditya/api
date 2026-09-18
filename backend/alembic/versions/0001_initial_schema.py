"""Initial DocuParse schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on PostgreSQL; plain JSON on SQLite so the same migration runs in tests.
JSON_TYPE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
ID = sa.String(40)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", ID, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("plan", sa.String(40), nullable=False, server_default="free"),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        sa.Column("monthly_document_quota", sa.Integer(), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("role", sa.String(40), nullable=False, server_default="owner"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_users_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    op.create_table(
        "api_keys",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("created_by_user_id", ID, nullable=True),
        sa.Column("name", sa.String(120), nullable=False, server_default="Default key"),
        sa.Column("environment", sa.String(10), nullable=False, server_default="live"),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(24), nullable=False),
        sa.Column("last_four", sa.String(4), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_used_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("rotated_from_id", ID, nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_api_keys_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_api_keys_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("ix_api_keys_organization_id", "api_keys", ["organization_id"])

    op.create_table(
        "documents",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("request_id", sa.String(40), nullable=True),
        sa.Column("filename", sa.String(400), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("storage_backend", sa.String(20), nullable=False, server_default="local"),
        sa.Column("storage_key", sa.String(500), nullable=True),
        sa.Column("document_type", sa.String(60), nullable=False, server_default="gst_invoice"),
        sa.Column("status", sa.String(20), nullable=False, server_default="received"),
        sa.Column("retention_expires_at", TS, nullable=True),
        sa.Column("purged_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_documents_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    op.create_index("ix_documents_organization_id", "documents", ["organization_id"])
    op.create_index("ix_documents_request_id", "documents", ["request_id"])
    op.create_index("ix_documents_checksum_sha256", "documents", ["checksum_sha256"])
    op.create_index("ix_documents_retention_expires_at", "documents", ["retention_expires_at"])
    op.create_index("ix_documents_org_created", "documents", ["organization_id", "created_at"])

    op.create_table(
        "extractions",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("document_id", ID, nullable=False),
        sa.Column("request_id", sa.String(40), nullable=True),
        sa.Column("document_type", sa.String(60), nullable=False, server_default="gst_invoice"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("data", JSON_TYPE, nullable=True),
        sa.Column("field_confidence", JSON_TYPE, nullable=True),
        sa.Column("overall_confidence", sa.Float(), nullable=True),
        sa.Column("provider", sa.String(60), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("provider_latency_ms", sa.Integer(), nullable=True),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_extractions_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_extractions_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extractions"),
    )
    op.create_index("ix_extractions_organization_id", "extractions", ["organization_id"])
    op.create_index("ix_extractions_document_id", "extractions", ["document_id"])
    op.create_index("ix_extractions_request_id", "extractions", ["request_id"])
    op.create_index("ix_extractions_org_created", "extractions", ["organization_id", "created_at"])

    op.create_table(
        "validation_results",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("extraction_id", ID, nullable=False),
        sa.Column("overall", sa.String(20), nullable=False),
        sa.Column("checks", JSON_TYPE, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_validation_results_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["extractions.id"],
            name="fk_validation_results_extraction_id_extractions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_validation_results"),
    )
    op.create_index(
        "ix_validation_results_organization_id", "validation_results", ["organization_id"]
    )
    op.create_index("ix_validation_results_extraction_id", "validation_results", ["extraction_id"])

    op.create_table(
        "usage_events",
        sa.Column("id", ID, nullable=False),
        sa.Column("organization_id", ID, nullable=False),
        sa.Column("api_key_id", ID, nullable=True),
        sa.Column("document_id", ID, nullable=True),
        sa.Column("request_id", sa.String(40), nullable=True),
        sa.Column("endpoint", sa.String(120), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False, server_default="api_request"),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("billable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(60), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_usage_events_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["api_keys.id"],
            name="fk_usage_events_api_key_id_api_keys",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usage_events"),
    )
    op.create_index("ix_usage_events_organization_id", "usage_events", ["organization_id"])
    op.create_index("ix_usage_events_request_id", "usage_events", ["request_id"])
    op.create_index(
        "ix_usage_events_org_created", "usage_events", ["organization_id", "created_at"]
    )
    op.create_index(
        "ix_usage_events_org_billable",
        "usage_events",
        ["organization_id", "billable", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("usage_events")
    op.drop_table("validation_results")
    op.drop_table("extractions")
    op.drop_table("documents")
    op.drop_table("api_keys")
    op.drop_table("users")
    op.drop_table("organizations")
