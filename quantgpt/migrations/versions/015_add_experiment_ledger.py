"""add experiment ledger

Revision ID: 015
Revises: 014
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", sa.String(120), nullable=False),
        sa.Column("vendor", sa.String(120), nullable=True),
        sa.Column("source_kind", sa.String(80), nullable=True),
        sa.Column("cache_path", sa.String(500), nullable=True),
        sa.Column("query_params", sa.JSON(), nullable=True),
        sa.Column("field_schema", sa.JSON(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("date_min", sa.String(10), nullable=True),
        sa.Column("date_max", sa.String(10), nullable=True),
        sa.Column("content_hash", sa.String(80), nullable=True),
        sa.Column("download_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_data_snapshots_snapshot_id", "data_snapshots", ["snapshot_id"], unique=True)
    op.create_index("ix_data_snapshots_content_hash", "data_snapshots", ["content_hash"])

    op.create_table(
        "experiments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", sa.String(80), nullable=False),
        sa.Column("run_id", sa.String(80), nullable=True),
        sa.Column("parent_run_id", sa.String(80), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("task_id", sa.String(12), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("parent_experiment_id", sa.String(80), nullable=True),
        sa.Column("factor_id", sa.String(80), nullable=True),
        sa.Column("factor_hash", sa.String(80), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("expression_normalized", sa.Text(), nullable=False),
        sa.Column("strategy_spec_version", sa.String(60), nullable=True),
        sa.Column("strategy_id", UUID(as_uuid=True), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("strategy_run_id", UUID(as_uuid=True), sa.ForeignKey("strategy_runs.id"), nullable=True),
        sa.Column("universe", sa.String(80), nullable=True),
        sa.Column("market", sa.String(60), nullable=True),
        sa.Column("asset_class", sa.String(60), nullable=True),
        sa.Column("data_source", sa.String(120), nullable=True),
        sa.Column("data_version", sa.String(120), nullable=True),
        sa.Column("data_snapshot_id", sa.String(120), nullable=True),
        sa.Column("adjustment_type", sa.String(40), nullable=True),
        sa.Column("industry_neutralization", sa.Boolean(), nullable=True),
        sa.Column("size_neutralization", sa.Boolean(), nullable=True),
        sa.Column("cost_model", sa.JSON(), nullable=True),
        sa.Column("rebalance_frequency", sa.String(40), nullable=True),
        sa.Column("holding_period", sa.Integer(), nullable=True),
        sa.Column("train_period", sa.JSON(), nullable=True),
        sa.Column("validation_period", sa.JSON(), nullable=True),
        sa.Column("test_period", sa.JSON(), nullable=True),
        sa.Column("direction_mode", sa.String(40), nullable=True),
        sa.Column("direction_policy", sa.String(60), nullable=True),
        sa.Column("research_mode", sa.String(60), nullable=True),
        sa.Column("random_seed", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="draft"),
        sa.Column("promotion_stage", sa.String(40), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=True),
        sa.Column("git_commit", sa.String(80), nullable=True),
        sa.Column("config_hash", sa.String(80), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_experiments_experiment_id", "experiments", ["experiment_id"], unique=True)
    op.create_index("ix_experiments_run_id", "experiments", ["run_id"])
    op.create_index("ix_experiments_parent_run_id", "experiments", ["parent_run_id"])
    op.create_index("ix_experiments_user_id", "experiments", ["user_id"])
    op.create_index("ix_experiments_task_id", "experiments", ["task_id"])
    op.create_index("ix_experiments_parent_experiment_id", "experiments", ["parent_experiment_id"])
    op.create_index("ix_experiments_factor_id", "experiments", ["factor_id"])
    op.create_index("ix_experiments_factor_hash", "experiments", ["factor_hash"])
    op.create_index("ix_experiments_strategy_id", "experiments", ["strategy_id"])
    op.create_index("ix_experiments_strategy_run_id", "experiments", ["strategy_run_id"])
    op.create_index("ix_experiments_data_snapshot_id", "experiments", ["data_snapshot_id"])
    op.create_index("ix_experiments_status", "experiments", ["status"])
    op.create_index("ix_experiments_config_hash", "experiments", ["config_hash"])
    op.create_index("ix_experiments_user_created", "experiments", ["user_id", "created_at"])
    op.create_index("ix_experiments_factor_status", "experiments", ["factor_hash", "status"])

    op.create_table(
        "factor_registry",
        sa.Column("factor_hash", sa.String(80), primary_key=True),
        sa.Column("expression_normalized", sa.Text(), nullable=False),
        sa.Column("family_key", sa.Text(), nullable=True),
        sa.Column("operator_family", sa.String(120), nullable=True),
        sa.Column("first_experiment_id", sa.String(80), nullable=True),
        sa.Column("latest_experiment_id", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_factor_registry_family_key", "factor_registry", ["family_key"])

    op.create_table(
        "experiment_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", sa.String(80), sa.ForeignKey("experiments.experiment_id"), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("validation_stage", sa.String(40), nullable=True),
        sa.Column("train_period", sa.JSON(), nullable=True),
        sa.Column("validation_period", sa.JSON(), nullable=True),
        sa.Column("test_period", sa.JSON(), nullable=True),
        sa.Column("direction_policy", sa.String(60), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("oos_score", sa.JSON(), nullable=True),
        sa.Column("data_quality", sa.JSON(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_experiment_results_experiment_id", "experiment_results", ["experiment_id"])
    op.create_index("ix_experiment_results_experiment_stage", "experiment_results", ["experiment_id", "stage"])

    op.create_table(
        "experiment_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", sa.String(80), sa.ForeignKey("experiments.experiment_id"), nullable=False),
        sa.Column("artifact_type", sa.String(60), nullable=False),
        sa.Column("uri", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(80), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_experiment_artifacts_experiment_id", "experiment_artifacts", ["experiment_id"])
    op.create_index("ix_experiment_artifacts_content_hash", "experiment_artifacts", ["content_hash"])

    op.create_table(
        "promotion_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", sa.String(80), sa.ForeignKey("experiments.experiment_id"), nullable=False),
        sa.Column("boundary", sa.String(40), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_promotion_events_experiment_id", "promotion_events", ["experiment_id"])
    op.create_index("ix_promotion_events_experiment_boundary", "promotion_events", ["experiment_id", "boundary"])

    op.create_table(
        "export_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", sa.String(80), sa.ForeignKey("experiments.experiment_id"), nullable=False),
        sa.Column("schema_version", sa.String(60), nullable=False),
        sa.Column("export_path", sa.String(500), nullable=True),
        sa.Column("payload_hash", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_export_events_experiment_id", "export_events", ["experiment_id"])
    op.create_index("ix_export_events_payload_hash", "export_events", ["payload_hash"])
    op.create_index("ix_export_events_experiment_schema", "export_events", ["experiment_id", "schema_version"])


def downgrade() -> None:
    op.drop_table("export_events")
    op.drop_table("promotion_events")
    op.drop_table("experiment_artifacts")
    op.drop_table("experiment_results")
    op.drop_table("factor_registry")
    op.drop_table("experiments")
    op.drop_table("data_snapshots")
