"""add admin system user

Revision ID: 014
Revises: 013
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO users (id, email, is_active, subscribe_weekly, created_at)
        VALUES (
            '00000000-0000-0000-0000-000000000003',
            'admin@system.internal',
            true,
            false,
            NOW()
        )
        ON CONFLICT (id) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM users WHERE id = '00000000-0000-0000-0000-000000000003'
    """)
