"""Persist coordinated research rounds and shared calculation leases.

Revision ID: 018
Revises: 017
"""

import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("research_rounds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), nullable=False),
        sa.Column("coordinator_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("coordinator_digest", sa.String(64), nullable=False),
        sa.Column("baseline_run_id", sa.Uuid(), sa.ForeignKey("strategy_runs.id"), nullable=True),
        sa.Column("selection_config", sa.JSON(), nullable=False),
        sa.Column("holdout", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("frozen", sa.JSON(), nullable=True),
        sa.Column("final_evaluation_id", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id"), sa.UniqueConstraint("final_evaluation_id"))
    op.create_table("research_directions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("round_id", sa.Uuid(), sa.ForeignKey("research_rounds.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("credential_digest", sa.String(64), nullable=False),
        sa.Column("finished", sa.Boolean(), nullable=False),
        sa.Column("legacy_sources", sa.JSON(), nullable=False),
        sa.UniqueConstraint("round_id", "name", name="uq_research_round_direction"))
    op.create_index("ix_research_directions_round_id", "research_directions", ["round_id"])
    op.create_table("evaluation_execution_leases",
        sa.Column("evaluation_id", sa.String(80), sa.ForeignKey("experiments.experiment_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("owner_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    connection = op.get_bind()
    tables = ("evaluation_execution_leases", "research_directions", "research_rounds")
    for table_name in tables:
        table = sa.table(table_name)
        if connection.scalar(sa.select(sa.literal(1)).select_from(table).limit(1)):
            raise RuntimeError("Research round or lease data exists: retain expanded schema and roll back application only")
    for table_name in tables:
        op.drop_table(table_name)
