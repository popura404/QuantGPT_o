"""create factor search attempts ledger

Revision ID: 013
Revises: 012
Create Date: 2026-05-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "factor_search_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_id", sa.String(12), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("parent_task_id", sa.String(12), nullable=True),
        sa.Column("generation_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("expression_key", sa.Text(), nullable=False),
        sa.Column("family_key", sa.Text(), nullable=False),
        sa.Column("scope_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("start_date", sa.String(10), nullable=True),
        sa.Column("end_date", sa.String(10), nullable=True),
        sa.Column("universe", sa.String(80), nullable=True),
        sa.Column("source_strategy", sa.String(30), nullable=True),
        sa.Column("from_mutation", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("from_crossover", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(30), nullable=False, server_default="generated"),
        sa.Column("failed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("failure_stage", sa.String(50), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("entered_next_round", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("raw_score", sa.Float(), nullable=True),
        sa.Column("selection_score", sa.Float(), nullable=True),
        sa.Column("search_penalty", sa.Float(), nullable=False, server_default="0"),
        sa.Column("prior_expression_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prior_family_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_factor_search_attempts_user_id", "factor_search_attempts", ["user_id"])
    op.create_index("ix_factor_search_attempts_task_id", "factor_search_attempts", ["task_id"])
    op.create_index("ix_factor_search_attempts_parent_task_id", "factor_search_attempts", ["parent_task_id"])
    op.create_index(
        "ix_factor_search_attempts_user_task_gen",
        "factor_search_attempts",
        ["user_id", "task_id", "generation_index"],
    )
    op.create_index("ix_factor_search_attempts_user_expr", "factor_search_attempts", ["user_id", "expression_key"])
    op.create_index("ix_factor_search_attempts_user_family", "factor_search_attempts", ["user_id", "family_key"])
    op.create_index("ix_factor_search_attempts_user_scope", "factor_search_attempts", ["user_id", "scope_key"])
    op.create_index(
        "ix_factor_search_attempts_scope_dates",
        "factor_search_attempts",
        ["user_id", "universe", "start_date", "end_date"],
    )


def downgrade() -> None:
    op.drop_table("factor_search_attempts")
