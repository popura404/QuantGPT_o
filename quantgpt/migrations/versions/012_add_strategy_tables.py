"""add strategy persistence tables

Revision ID: 012
Revises: 011
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("market", sa.String(60), nullable=False),
        sa.Column("universe", sa.String(80), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(30), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategies_user_id", "strategies", ["user_id"])
    op.create_index("ix_strategies_market", "strategies", ["market"])
    op.create_index("ix_strategies_user_market", "strategies", ["user_id", "market"])

    op.create_table(
        "strategy_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_id", UUID(as_uuid=True), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_id", sa.String(12), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("report_url", sa.String(500), nullable=True),
        sa.Column("summary_json", sa.String(500), nullable=True),
        sa.Column("signal_export", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategy_runs_strategy_id", "strategy_runs", ["strategy_id"])
    op.create_index("ix_strategy_runs_user_id", "strategy_runs", ["user_id"])
    op.create_index("ix_strategy_runs_task_id", "strategy_runs", ["task_id"])


def downgrade() -> None:
    op.drop_table("strategy_runs")
    op.drop_table("strategies")
