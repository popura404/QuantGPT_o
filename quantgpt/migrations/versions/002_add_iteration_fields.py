"""add iteration fields to tasks

Revision ID: 002
Revises: 001
Create Date: 2026-03-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("task_type", sa.String(20), nullable=True, server_default="backtest"))
        batch.add_column(sa.Column("parent_task_id", sa.String(12), nullable=True))
        batch.create_foreign_key("fk_tasks_parent_task", "tasks", ["parent_task_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("parent_task_id")
        batch.drop_column("task_type")
