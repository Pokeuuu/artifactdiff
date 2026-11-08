"""Initial schema: comparisons table.

Revision ID: 0001
Revises:
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "comparisons",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("job_status", sa.String(16), nullable=False),
        sa.Column("result_status", sa.String(16)),
        sa.Column("plugin", sa.String(64)),
        sa.Column("plugin_version", sa.String(32)),
        sa.Column("requested_plugin", sa.String(64)),
        sa.Column("old_name", sa.String(255), nullable=False),
        sa.Column("new_name", sa.String(255), nullable=False),
        sa.Column("old_size", sa.Integer, nullable=False),
        sa.Column("new_size", sa.Integer, nullable=False),
        sa.Column("total_changes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_severity", sa.String(16)),
        sa.Column("summary", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("options_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text),
        sa.Column("duration_ms", sa.Float),
        sa.Column("inputs_expire_at", sa.DateTime(timezone=True)),
        sa.Column("inputs_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("assets_bytes", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_comparisons_created_at", "comparisons", ["created_at"])
    op.create_index("ix_comparisons_job_status", "comparisons", ["job_status"])
    op.create_index("ix_comparisons_plugin", "comparisons", ["plugin"])


def downgrade() -> None:
    op.drop_table("comparisons")
