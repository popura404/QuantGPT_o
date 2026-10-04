"""add MCP system user

Revision ID: 010
Revises: 009
Create Date: 2026-03-23
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        INSERT INTO users (id, email, is_active, subscribe_weekly, created_at)
        VALUES (
            :system_id,
            'mcp@system.internal',
            true,
            false,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (id) DO NOTHING
    """).bindparams(sa.bindparam("system_id", value=uuid.UUID("00000000-0000-0000-0000-000000000002"), type_=sa.Uuid())))


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM users WHERE id = :system_id
    """).bindparams(sa.bindparam("system_id", value=uuid.UUID("00000000-0000-0000-0000-000000000002"), type_=sa.Uuid())))
