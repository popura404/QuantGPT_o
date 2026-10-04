"""SQLAlchemy ORM models for QuantGPT."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchProject(Base):
    __tablename__ = "research_projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    market: Mapped[str] = mapped_column(String(60), default="us_equity")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_projects.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="researcher")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ResearchAuditEvent(Base):
    __tablename__ = "research_audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_projects.id"), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FinalWindowExposure(Base):
    """Unique project/family/window lock, independent of mutable evaluation IDs."""

    __tablename__ = "final_window_exposures"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_projects.id"), primary_key=True)
    family_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    window_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    evaluation_hash: Mapped[str] = mapped_column(String(64))
    evaluation_id: Mapped[str] = mapped_column(String(80))
    frozen_config: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)  # bcrypt, NULL=未设置密码
    nickname = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    subscribe_weekly = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    sessions = relationship("Session", back_populates="user", lazy="selectin")
    tasks = relationship("Task", back_populates="user", lazy="selectin")
    reports = relationship("Report", back_populates="user", lazy="selectin")
    strategies = relationship("Strategy", back_populates="user", lazy="selectin")


class VerificationCode(Base):
    __tablename__ = "verification_codes"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_verification_codes_email_used", "email", "used"),
    )


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=True)
    market = Column(String(20), default="a_share", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User", back_populates="sessions")
    tasks = relationship("Task", back_populates="session", lazy="selectin")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(12), primary_key=True)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("research_projects.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    progress_json: Mapped[dict | None] = mapped_column(JSON)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    remote_run_ref: Mapped[str | None] = mapped_column(String(200))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    attempt_id: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_pending: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    session_id = Column(Uuid, ForeignKey("sessions.id"), nullable=True, index=True)
    status = Column(String(30), nullable=False, default="pending")
    task_type = Column(String(50), nullable=True, default="backtest")
    parent_task_id = Column(String(12), ForeignKey("tasks.id"), nullable=True)
    params = Column(JSON, nullable=True)
    expression = Column(Text, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User", back_populates="tasks")
    session = relationship("Session", back_populates="tasks")
    reports = relationship("Report", back_populates="task", lazy="selectin")

    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_task_user_idempotency"),)


class Report(Base):
    __tablename__ = "reports"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String(12), ForeignKey("tasks.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User", back_populates="reports")
    task = relationship("Task", back_populates="reports")


class SavedFactor(Base):
    __tablename__ = "saved_factors"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String(12), ForeignKey("tasks.id"), nullable=True)
    expression = Column(Text, nullable=False)
    name = Column(String(200), nullable=True)       # 用户自定义名称
    note = Column(Text, nullable=True)              # 备注
    tags = Column(JSON, nullable=True)              # 标签列表
    metrics = Column(JSON, nullable=True)           # 快照：report_metrics
    backtest_summary = Column(JSON, nullable=True)  # 快照：backtest_summary
    params = Column(JSON, nullable=True)            # 回测参数
    report_url = Column(String(500), nullable=True)
    market = Column(String(20), default="a_share", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User")


class FactorPoolEntry(Base):
    __tablename__ = "factor_pool_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("research_projects.id"), index=True)
    definition_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(80), index=True)
    legacy_saved_factor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, unique=True)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    expression_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    main_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    category_tag: Mapped[str] = mapped_column(String(120), default="category:uncategorized", nullable=False, index=True)
    pool_status: Mapped[str] = mapped_column(String(40), default="watchlist", nullable=False, index=True)
    factor_hash: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    experiment_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    market: Mapped[str] = mapped_column(String(60), default="a_share", nullable=False)
    universe: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    holding_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    backtest_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    factor_card_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    owner = relationship("User")

    __table_args__ = (
        Index("ix_factor_pool_owner_status", "owner_user_id", "pool_status"),
        Index("ix_factor_pool_owner_category", "owner_user_id", "category_tag"),
        Index("ix_factor_pool_owner_hash", "owner_user_id", "factor_hash"),
        Index("ix_factor_pool_owner_expr", "owner_user_id", "expression_normalized"),
        Index("ix_factor_pool_owner_universe", "owner_user_id", "universe"),
        Index("ix_factor_pool_owner_created", "owner_user_id", "created_at"),
    )


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    description = Column(Text, nullable=False)
    screenshot_path = Column(String(500), nullable=True)
    task_id = Column(String(12), nullable=True)
    user_agent = Column(String(500), nullable=True)
    page_url = Column(String(500), nullable=True)
    webhook_sent = Column(Boolean, default=False, nullable=False)
    resolved = Column(Boolean, default=False, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User")


class SubmittedAlpha(Base):
    __tablename__ = "submitted_alphas"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    alpha_id = Column(String(50), nullable=False, index=True)
    expression = Column(Text, nullable=False)
    expression_normalized = Column(Text, nullable=True)
    region = Column(String(10), nullable=False, default="USA")
    universe = Column(String(20), nullable=False, default="TOP3000")
    delay = Column(Integer, nullable=False, default=1)
    decay = Column(Integer, nullable=False, default=0)
    neutralization = Column(String(30), nullable=False, default="SUBINDUSTRY")
    truncation = Column(Float, nullable=False, default=0.08)
    tag = Column(String(100), nullable=True)
    sharpe = Column(Float, nullable=True)
    fitness = Column(Float, nullable=True)
    returns = Column(Float, nullable=True)
    turnover = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="submitted")
    submitted_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User")

    __table_args__ = (
        Index("ix_submitted_alphas_user_expr", "user_id", "expression_normalized"),
    )


class FactorSearchAttempt(Base):
    __tablename__ = "factor_search_attempts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String(12), ForeignKey("tasks.id"), nullable=True, index=True)
    parent_task_id = Column(String(12), nullable=True, index=True)
    generation_index = Column(Integer, nullable=False, default=0)
    expression = Column(Text, nullable=False)
    expression_key = Column(Text, nullable=False)
    family_key = Column(Text, nullable=False)
    scope_key = Column(String(255), nullable=False, default="")
    params = Column(JSON, nullable=True)
    start_date = Column(String(10), nullable=True)
    end_date = Column(String(10), nullable=True)
    universe = Column(String(80), nullable=True)
    source_strategy = Column(String(30), nullable=True)
    from_mutation = Column(Boolean, default=False, nullable=False)
    from_crossover = Column(Boolean, default=False, nullable=False)
    status = Column(String(30), nullable=False, default="generated")
    failed = Column(Boolean, default=False, nullable=False)
    failure_stage = Column(String(50), nullable=True)
    failure_reason = Column(Text, nullable=True)
    entered_next_round = Column(Boolean, default=False, nullable=False)
    raw_score = Column(Float, nullable=True)
    selection_score = Column(Float, nullable=True)
    search_penalty = Column(Float, default=0.0, nullable=False)
    prior_expression_attempts = Column(Integer, default=0, nullable=False)
    prior_family_attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User")
    task = relationship("Task")

    __table_args__ = (
        Index("ix_factor_search_attempts_user_task_gen", "user_id", "task_id", "generation_index"),
        Index("ix_factor_search_attempts_user_expr", "user_id", "expression_key"),
        Index("ix_factor_search_attempts_user_family", "user_id", "family_key"),
        Index("ix_factor_search_attempts_user_scope", "user_id", "scope_key"),
        Index("ix_factor_search_attempts_scope_dates", "user_id", "universe", "start_date", "end_date"),
    )


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id = Column(String(80), nullable=False, unique=True, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("research_projects.id"), index=True)
    definition_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    evaluation_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    evaluation_config: Mapped[dict | None] = mapped_column(JSON)
    evidence_status: Mapped[str | None] = mapped_column(String(40))
    backend: Mapped[str | None] = mapped_column(String(30))
    supersedes_evaluation_id: Mapped[str | None] = mapped_column(String(80))
    run_id = Column(String(80), nullable=True, index=True)
    parent_run_id = Column(String(80), nullable=True, index=True)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=True, index=True)
    task_id = Column(String(12), ForeignKey("tasks.id"), nullable=True, index=True)
    parent_experiment_id = Column(String(80), nullable=True, index=True)
    factor_id = Column(String(80), nullable=True, index=True)
    factor_hash = Column(String(80), nullable=False, index=True)
    expression = Column(Text, nullable=False)
    expression_normalized = Column(Text, nullable=False)
    strategy_spec_version = Column(String(60), nullable=True)
    strategy_id = Column(Uuid, ForeignKey("strategies.id"), nullable=True, index=True)
    strategy_run_id = Column(Uuid, ForeignKey("strategy_runs.id"), nullable=True, index=True)
    universe = Column(String(80), nullable=True)
    market = Column(String(60), nullable=True)
    asset_class = Column(String(60), nullable=True)
    data_source = Column(String(120), nullable=True)
    data_version = Column(String(120), nullable=True)
    data_snapshot_id = Column(String(120), nullable=True, index=True)
    adjustment_type = Column(String(40), nullable=True)
    industry_neutralization = Column(Boolean, nullable=True)
    size_neutralization = Column(Boolean, nullable=True)
    cost_model = Column(JSON, nullable=True)
    rebalance_frequency = Column(String(40), nullable=True)
    holding_period = Column(Integer, nullable=True)
    train_period = Column(JSON, nullable=True)
    validation_period = Column(JSON, nullable=True)
    test_period = Column(JSON, nullable=True)
    direction_mode = Column(String(40), nullable=True)
    direction_policy = Column(String(60), nullable=True)
    research_mode = Column(String(60), nullable=True)
    random_seed = Column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)
    promotion_stage = Column(String(40), nullable=True)
    created_by = Column(String(80), nullable=True)
    git_commit = Column(String(80), nullable=True)
    config_hash = Column(String(80), nullable=True, index=True)
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User")
    task = relationship("Task")
    results = relationship("ExperimentResult", back_populates="experiment", lazy="selectin")
    artifacts = relationship("ExperimentArtifact", back_populates="experiment", lazy="selectin")
    promotion_events = relationship("PromotionEvent", back_populates="experiment", lazy="selectin")
    export_events = relationship("ExportEvent", back_populates="experiment", lazy="selectin")

    __table_args__ = (
        Index("ix_experiments_user_created", "user_id", "created_at"),
        Index("ix_experiments_factor_status", "factor_hash", "status"),
    )


class ExperimentResult(Base):
    __tablename__ = "experiment_results"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id = Column(String(80), ForeignKey("experiments.experiment_id"), nullable=False, index=True)
    stage = Column(String(40), nullable=False)
    validation_stage = Column(String(40), nullable=True)
    train_period = Column(JSON, nullable=True)
    validation_period = Column(JSON, nullable=True)
    test_period = Column(JSON, nullable=True)
    direction_policy = Column(String(60), nullable=True)
    metrics = Column(JSON, nullable=True)
    oos_score = Column(JSON, nullable=True)
    data_quality = Column(JSON, nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    experiment = relationship("Experiment", back_populates="results")

    __table_args__ = (
        Index("ix_experiment_results_experiment_stage", "experiment_id", "stage"),
    )


class ExperimentArtifact(Base):
    __tablename__ = "experiment_artifacts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id = Column(String(80), ForeignKey("experiments.experiment_id"), nullable=False, index=True)
    artifact_type = Column(String(60), nullable=False)
    uri = Column(String(500), nullable=False)
    content_hash = Column(String(80), nullable=True, index=True)
    artifact_metadata = Column("metadata", JSON, nullable=True)
    payload: Mapped[dict | list | None] = mapped_column(JSON)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    experiment = relationship("Experiment", back_populates="artifacts")


class FactorRegistry(Base):
    __tablename__ = "factor_registry"

    factor_hash = Column(String(80), primary_key=True)
    expression_normalized = Column(Text, nullable=False)
    family_key = Column(Text, nullable=True, index=True)
    operator_family = Column(String(120), nullable=True)
    first_experiment_id = Column(String(80), nullable=True)
    latest_experiment_id = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    snapshot_id = Column(String(120), nullable=False, unique=True, index=True)
    vendor = Column(String(120), nullable=True)
    source_kind = Column(String(80), nullable=True)
    cache_path = Column(String(500), nullable=True)
    query_params = Column(JSON, nullable=True)
    field_schema = Column(JSON, nullable=True)
    row_count = Column(Integer, nullable=True)
    date_min = Column(String(10), nullable=True)
    date_max = Column(String(10), nullable=True)
    content_hash = Column(String(80), nullable=True, index=True)
    download_time = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class PromotionEvent(Base):
    __tablename__ = "promotion_events"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id = Column(String(80), ForeignKey("experiments.experiment_id"), nullable=False, index=True)
    boundary = Column(String(40), nullable=False)
    decision = Column(String(40), nullable=False)
    blockers = Column(JSON, nullable=True)
    provenance = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    experiment = relationship("Experiment", back_populates="promotion_events")

    __table_args__ = (
        Index("ix_promotion_events_experiment_boundary", "experiment_id", "boundary"),
    )


class ExportEvent(Base):
    __tablename__ = "export_events"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id = Column(String(80), ForeignKey("experiments.experiment_id"), nullable=False, index=True)
    schema_version = Column(String(60), nullable=False)
    export_path = Column(String(500), nullable=True)
    payload_hash = Column(String(80), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    experiment = relationship("Experiment", back_populates="export_events")

    __table_args__ = (
        Index("ix_export_events_experiment_schema", "experiment_id", "schema_version"),
    )


class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    date = Column(String(10), nullable=False)          # "2026-03-24"
    market = Column(String(20), default="a_share", nullable=False)
    title = Column(String(200), nullable=True)
    content = Column(Text, nullable=True)              # markdown
    metrics = Column(JSON, nullable=True)              # index changes, volume, etc.
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_daily_summaries_date_market", "date", "market", unique=True),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    key_hash = Column(String(255), nullable=False, unique=True)
    prefix = Column(String(10), nullable=False)
    name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User")


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("research_projects.id"), index=True)
    name = Column(String(120), nullable=False)
    schema_version = Column(String(40), nullable=False)
    market = Column(String(60), nullable=False, index=True)
    universe = Column(String(80), nullable=False)
    spec = Column(JSON, nullable=False)
    tags = Column(JSON, nullable=True)
    status = Column(String(30), default="active", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User", back_populates="strategies")
    runs = relationship("StrategyRun", back_populates="strategy", lazy="selectin")

    __table_args__ = (
        Index("ix_strategies_user_market", "user_id", "market"),
    )


class StrategyRun(Base):
    __tablename__ = "strategy_runs"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    strategy_id = Column(Uuid, ForeignKey("strategies.id"), nullable=True, index=True)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String(12), ForeignKey("tasks.id"), nullable=True, index=True)
    result = Column(JSON, nullable=False)
    report_url = Column(String(500), nullable=True)
    summary_json = Column(String(500), nullable=True)
    signal_export = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    strategy = relationship("Strategy", back_populates="runs")
    user = relationship("User")
    task = relationship("Task")
