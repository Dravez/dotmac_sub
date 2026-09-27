"""Add clear failure explanations and administrator retry history.

Revision ID: 625_automation_run_retry_audit
Revises: 624_native_prepaid_opening_repairs
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "625_automation_run_retry_audit"
down_revision: str | None = "624_native_prepaid_opening_repairs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.add_column("automation_runs", sa.Column("error_message", sa.String(length=1000)))
    op.add_column(
        "automation_step_runs", sa.Column("error_message", sa.String(length=1000))
    )
    op.create_table(
        "automation_run_retries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("command_id", sa.UUID(), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default="running", nullable=False
        ),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("resulting_run_status", sa.String(length=24), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_number > 0", name="ck_automation_run_retries_attempt"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_automation_run_retries_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["automation_runs.tenant_id", "automation_runs.id"],
            ondelete="CASCADE",
            name="fk_automation_run_retries_run_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_automation_run_retries_tenant_id"
        ),
        sa.UniqueConstraint(
            "run_id", "attempt_number", name="uq_automation_run_retries_run_attempt"
        ),
        sa.UniqueConstraint("command_id", name="uq_automation_run_retries_command"),
    )
    op.create_index(
        "ix_automation_run_retries_tenant_id",
        "automation_run_retries",
        ["tenant_id"],
    )
    op.create_index(
        "ix_automation_run_retries_run_id", "automation_run_retries", ["run_id"]
    )
    op.execute("ALTER TABLE automation_run_retries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE automation_run_retries FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY automation_run_retries_tenant_isolation
          ON automation_run_retries
          USING (tenant_id = app_current_tenant_id())
          WITH CHECK (tenant_id = app_current_tenant_id())
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON automation_run_retries TO app_user"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON automation_run_retries TO platform_api"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS automation_run_retries_tenant_isolation "
        "ON automation_run_retries"
    )
    op.drop_index(
        "ix_automation_run_retries_run_id", table_name="automation_run_retries"
    )
    op.drop_index(
        "ix_automation_run_retries_tenant_id", table_name="automation_run_retries"
    )
    op.drop_table("automation_run_retries")
    op.drop_column("automation_step_runs", "error_message")
    op.drop_column("automation_runs", "error_message")
