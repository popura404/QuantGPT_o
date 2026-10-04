"""add admin system user

Revision ID: 014
Revises: 013
Create Date: 2026-05-20
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        INSERT INTO users (id, email, is_active, subscribe_weekly, created_at)
        VALUES (
            :system_id,
            'admin@system.internal',
            true,
            false,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (id) DO NOTHING
    """).bindparams(sa.bindparam("system_id", value=uuid.UUID("00000000-0000-0000-0000-000000000003"), type_=sa.Uuid())))


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM users WHERE id = :system_id
    """).bindparams(sa.bindparam("system_id", value=uuid.UUID("00000000-0000-0000-0000-000000000003"), type_=sa.Uuid())))
