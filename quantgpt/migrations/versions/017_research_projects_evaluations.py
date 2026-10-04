"""Expand project and evaluation identity without reinterpreting legacy hashes.

Revision ID: 017
Revises: 016
"""

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("research_projects",
                    sa.Column("id", sa.Uuid(), primary_key=True),
                    sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("name", sa.String(200), nullable=False),
                    sa.Column("market", sa.String(60), nullable=False),
                    sa.Column("config", sa.JSON(), nullable=False),
                    sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_research_projects_owner_user_id", "research_projects", ["owner_user_id"])
    op.create_table("project_members",
                    sa.Column("project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), primary_key=True),
                    sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
                    sa.Column("role", sa.String(20), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("research_audit_events",
                    sa.Column("id", sa.Uuid(), primary_key=True),
                    sa.Column("project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), nullable=False),
                    sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("action", sa.String(80), nullable=False),
                    sa.Column("payload", sa.JSON(), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_research_audit_events_project_id", "research_audit_events", ["project_id"])
    op.create_table("final_window_exposures",
                    sa.Column("project_id", sa.Uuid(), sa.ForeignKey("research_projects.id"), primary_key=True),
                    sa.Column("family_key", sa.String(160), primary_key=True),
                    sa.Column("window_key", sa.String(64), primary_key=True),
                    sa.Column("evaluation_hash", sa.String(64), nullable=False),
                    sa.Column("evaluation_id", sa.String(80), nullable=False),
                    sa.Column("frozen_config", sa.JSON(), nullable=False),
                    sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for table in ("factor_pool_entries", "experiments", "strategies", "tasks"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("project_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(f"fk_{table}_project", "research_projects", ["project_id"], ["id"])
            batch.create_index(f"ix_{table}_project_id", ["project_id"])
    with op.batch_alter_table("factor_pool_entries") as batch:
        for name, kind in (("definition_hash", sa.String(64)), ("evaluation_id", sa.String(80)),
                           ("legacy_saved_factor_id", sa.Uuid())):
            batch.add_column(sa.Column(name, kind, nullable=True))
        batch.create_index("ix_factor_pool_entries_definition_hash", ["definition_hash"])
        batch.create_index("ix_factor_pool_entries_evaluation_id", ["evaluation_id"])
        batch.create_unique_constraint("uq_factor_pool_legacy_saved_factor", ["legacy_saved_factor_id"])
    with op.batch_alter_table("experiments") as batch:
        for name, kind in (("definition_hash", sa.String(64)), ("evaluation_hash", sa.String(64)),
                           ("evaluation_config", sa.JSON()), ("evidence_status", sa.String(40)),
                           ("backend", sa.String(30)), ("supersedes_evaluation_id", sa.String(80))):
            batch.add_column(sa.Column(name, kind, nullable=True))
        batch.create_index("ix_experiments_definition_hash", ["definition_hash"])
        batch.create_index("ix_experiments_evaluation_hash", ["evaluation_hash"])
    with op.batch_alter_table("experiment_artifacts") as batch:
        batch.add_column(sa.Column("payload", sa.JSON(), nullable=True))
    with op.batch_alter_table("tasks") as batch:
        for name, kind in (("progress_json", sa.JSON()), ("idempotency_key", sa.String(160)),
                           ("request_hash", sa.String(64)), ("remote_run_ref", sa.String(200)),
                           ("attempt_id", sa.String(64)), ("lease_expires_at", sa.DateTime(timezone=True))):
            batch.add_column(sa.Column(name, kind, nullable=True))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("dispatch_pending", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.create_unique_constraint("uq_task_user_idempotency", ["user_id", "idempotency_key"])


def downgrade() -> None:
    # Application rollback on the expanded schema is safe. A destructive schema
    # downgrade is only allowed before any new research has been written.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM research_projects")):
        raise RuntimeError("New project data exists: retain expanded schema and roll back application only")
    if connection.scalar(sa.text("SELECT COUNT(*) FROM tasks WHERE request_hash IS NOT NULL "
                                 "OR attempt_id IS NOT NULL OR remote_run_ref IS NOT NULL")):
        raise RuntimeError("New durable task data exists: retain expanded schema and roll back application only")
    with op.batch_alter_table("experiment_artifacts") as batch:
        batch.drop_column("payload")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("uq_task_user_idempotency", type_="unique")
        for name in ("progress_json", "idempotency_key", "request_hash", "remote_run_ref", "attempt_id",
                     "lease_expires_at", "revision", "cancel_requested", "retryable", "dispatch_pending"):
            batch.drop_column(name)
    with op.batch_alter_table("experiments") as batch:
        batch.drop_index("ix_experiments_definition_hash")
        batch.drop_index("ix_experiments_evaluation_hash")
        for name in ("definition_hash", "evaluation_hash", "evaluation_config", "evidence_status", "backend",
                     "supersedes_evaluation_id"):
            batch.drop_column(name)
    with op.batch_alter_table("factor_pool_entries") as batch:
        batch.drop_index("ix_factor_pool_entries_definition_hash")
        batch.drop_index("ix_factor_pool_entries_evaluation_id")
        batch.drop_constraint("uq_factor_pool_legacy_saved_factor", type_="unique")
        for name in ("definition_hash", "evaluation_id", "legacy_saved_factor_id"):
            batch.drop_column(name)
    for table in ("tasks", "strategies", "experiments", "factor_pool_entries"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_project_id")
            batch.drop_constraint(f"fk_{table}_project", type_="foreignkey")
            batch.drop_column("project_id")
    for table in ("final_window_exposures", "research_audit_events", "project_members", "research_projects"):
        op.drop_table(table)
