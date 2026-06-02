"""create factor pool entries

Revision ID: 016
Revises: 015
Create Date: 2026-06-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "factor_pool_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("expression_normalized", sa.Text(), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("main_reason", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("category_tag", sa.String(120), nullable=False, server_default="category:uncategorized"),
        sa.Column("pool_status", sa.String(40), nullable=False, server_default="watchlist"),
        sa.Column("factor_hash", sa.String(80), nullable=True),
        sa.Column("experiment_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(12), nullable=True),
        sa.Column("market", sa.String(60), nullable=False, server_default="a_share"),
        sa.Column("universe", sa.String(80), nullable=True),
        sa.Column("holding_period", sa.Integer(), nullable=True),
        sa.Column("validation_stage", sa.String(40), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("backtest_summary", sa.JSON(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("validation_provenance", sa.JSON(), nullable=True),
        sa.Column("report_url", sa.String(500), nullable=True),
        sa.Column("factor_card_path", sa.String(500), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_factor_pool_entries_owner_user_id", "factor_pool_entries", ["owner_user_id"])
    op.create_index("ix_factor_pool_entries_category_tag", "factor_pool_entries", ["category_tag"])
    op.create_index("ix_factor_pool_entries_pool_status", "factor_pool_entries", ["pool_status"])
    op.create_index("ix_factor_pool_entries_factor_hash", "factor_pool_entries", ["factor_hash"])
    op.create_index("ix_factor_pool_entries_experiment_id", "factor_pool_entries", ["experiment_id"])
    op.create_index("ix_factor_pool_entries_task_id", "factor_pool_entries", ["task_id"])
    op.create_index("ix_factor_pool_entries_universe", "factor_pool_entries", ["universe"])
    op.create_index("ix_factor_pool_owner_status", "factor_pool_entries", ["owner_user_id", "pool_status"])
    op.create_index("ix_factor_pool_owner_category", "factor_pool_entries", ["owner_user_id", "category_tag"])
    op.create_index("ix_factor_pool_owner_hash", "factor_pool_entries", ["owner_user_id", "factor_hash"])
    op.create_index("ix_factor_pool_owner_expr", "factor_pool_entries", ["owner_user_id", "expression_normalized"])
    op.create_index("ix_factor_pool_owner_universe", "factor_pool_entries", ["owner_user_id", "universe"])
    op.create_index("ix_factor_pool_owner_created", "factor_pool_entries", ["owner_user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_factor_pool_owner_created", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_owner_universe", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_owner_expr", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_owner_hash", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_owner_category", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_owner_status", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_entries_universe", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_entries_task_id", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_entries_experiment_id", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_entries_factor_hash", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_entries_pool_status", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_entries_category_tag", table_name="factor_pool_entries")
    op.drop_index("ix_factor_pool_entries_owner_user_id", table_name="factor_pool_entries")
    op.drop_table("factor_pool_entries")
