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
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
