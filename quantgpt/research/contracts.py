"""Public, fail-closed contracts for reproducible research.

These models describe inputs and evidence identity. They do not authenticate a
caller, run a simulation, or certify a provider merely because a DTO validates.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SEMANTICS_VERSION = "factor_semantics/v2"
OPERATOR_SEMANTICS_VERSION = SEMANTICS_VERSION
FIELD_CONTRACT_VERSION = "research_fields/v1"
RESEARCH_CONTRACT_VERSION = "research_contract/v1"
EVENT_ORDER = (
    "previous_close_positions",
    "corporate_actions_and_entitlements",
    "open_fills_and_fees",
    "close_valuation",
    "after_close_signal",
)
FORBIDDEN_LIVE_FIELDS = frozenset({"execution", "broker", "account", "order", "api_key", "python_code", "script", "callback_url"})
SHA256_PATTERN = r"^[a-f0-9]{64}$"
CapabilityStatus = Literal["available", "unavailable", "unknown"]
ResearchDecision = Literal["not_evaluated", "accepted", "watchlist", "rejected", "insufficient_data"]


def _reject_live_fields(value: object) -> None:
    if isinstance(value, Mapping):
        forbidden = FORBIDDEN_LIVE_FIELDS.intersection(value)
        if forbidden:
            raise ValueError(f"Forbidden live execution fields: {sorted(forbidden)}")
        for child in value.values():
            _reject_live_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_live_fields(child)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def reject_live_fields(cls, value: Any) -> Any:
        _reject_live_fields(value)
        return value


class ResearchError(ContractModel):
    error_code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    next_action: str = Field(min_length=1)
    affected_fields: tuple[str, ...] = ()


class CapabilityBlockerError(ValueError):
    """Structured preflight failure, never permission to substitute a proxy."""

    def __init__(self, blockers: Sequence[ResearchError]):
        self.blockers = tuple(blockers)
        super().__init__("; ".join(f"{item.error_code}: {item.message}" for item in self.blockers))


class CapabilityReport(ContractModel):
    backend: str = Field(min_length=1)
    fields: dict[str, CapabilityStatus] = Field(default_factory=dict)
    features: dict[str, CapabilityStatus] = Field(default_factory=dict)
    blockers: tuple[ResearchError, ...] = ()
    external_integration: Literal["not_run", "verified", "blocked"] = "not_run"

    def require_fields(self, names: Sequence[str]) -> None:
        blockers = list(self.blockers)
        for name in sorted(set(names)):
            status = self.fields.get(name, "unknown")
            if status != "available":
                blockers.append(ResearchError(
                    error_code="CAPABILITY_FIELD_UNAVAILABLE" if status == "unavailable" else "CAPABILITY_FIELD_UNKNOWN",
                    message=f"{self.backend}: required field {name!r} is {status}",
                    next_action="Provide the same economic field with verified units and point-in-time availability.",
                    affected_fields=(name,),
                ))
        if blockers:
            raise CapabilityBlockerError(blockers)

    def require_neutralization(self, *, industry: bool = False, market_cap: bool = False) -> None:
        names = (["industry"] if industry else []) + (["market_cap"] if market_cap else [])
        self.require_fields(names)


class FieldContract(ContractModel):
    name: str = Field(min_length=1)
    dtype: Literal["float64", "int64", "string", "boolean", "timestamp", "date"]
    unit: str = Field(min_length=1)
    currency: str | None = None
    economic_meaning: str = Field(min_length=1)
    available_at_rule: str = Field(min_length=1)
    adjustment: Literal["raw", "split_adjusted", "total_return", "not_applicable"] = "not_applicable"
    period: Literal["instant", "session", "quarter", "annual", "ttm", "not_applicable"] = "not_applicable"
    requires_vintage: bool = False
    version: str = FIELD_CONTRACT_VERSION


US_RESEARCH_FIELDS = (
    FieldContract(name="security_id", dtype="string", unit="identifier", economic_meaning="Stable security identity, not ticker", available_at_rule="security master effective interval known by signal time"),
    FieldContract(name="open", dtype="float64", unit="USD/share", currency="USD", economic_meaning="Unadjusted official session open", available_at_rule="after session open and provider dissemination", adjustment="raw", period="session"),
    FieldContract(name="close", dtype="float64", unit="USD/share", currency="USD", economic_meaning="Unadjusted official session close", available_at_rule="after session close and provider dissemination", adjustment="raw", period="session"),
    FieldContract(name="volume", dtype="float64", unit="shares", economic_meaning="Session traded shares, not currency turnover", available_at_rule="after session close and provider dissemination", adjustment="raw", period="session"),
    FieldContract(name="shares_outstanding", dtype="float64", unit="shares", economic_meaning="Outstanding common shares on the same split basis as raw price", available_at_rule="public_at plus provider latency; latest vintage with available_at <= signal_time", period="instant", requires_vintage=True),
    FieldContract(name="market_cap", dtype="float64", unit="USD", currency="USD", economic_meaning="Raw share price times same-basis point-in-time shares outstanding", available_at_rule="max(price.available_at, shares.available_at)", period="instant", requires_vintage=True),
    FieldContract(name="industry", dtype="string", unit="classification_code", economic_meaning="Versioned industry membership effective at session", available_at_rule="effective_from <= session and available_at <= signal_time", period="instant", requires_vintage=True),
    FieldContract(name="revenue", dtype="float64", unit="USD", currency="USD", economic_meaning="Reported consolidated revenue for a discrete quarter, not profit", available_at_rule="filing public_at plus provider latency; date-only publication usable no earlier than next session open", period="quarter", requires_vintage=True),
    FieldContract(name="net_income", dtype="float64", unit="USD", currency="USD", economic_meaning="Reported net income for a discrete quarter, not operating income or cash flow", available_at_rule="filing public_at plus provider latency; date-only publication usable no earlier than next session open", period="quarter", requires_vintage=True),
    FieldContract(name="equity", dtype="float64", unit="USD", currency="USD", economic_meaning="Reported balance-sheet equity, not assets", available_at_rule="filing public_at plus provider latency; historical vintage selection", period="instant", requires_vintage=True),
    FieldContract(name="suspended", dtype="boolean", unit="boolean", economic_meaning="Known inability to trade in the session", available_at_rule="exchange status effective time and dissemination time"),
)


class Session(ContractModel):
    session: date
    open_at: datetime
    close_at: datetime

    @model_validator(mode="after")
    def check_times(self) -> Session:
        if self.open_at.utcoffset() is None or self.close_at.utcoffset() is None:
            raise ValueError("Session timestamps must be timezone-aware")
        if self.open_at >= self.close_at:
            raise ValueError("Session open must precede close")
        return self


class TradingCalendar(Protocol):
    """Versioned real exchange sessions, including holidays and early closes."""

    calendar_id: str
    version: str
    timezone: str

    def sessions(self, start: date, end: date) -> Sequence[Session]: ...

    def next_session(self, session: date) -> Session: ...


class ArtifactRef(ContractModel):
    schema_version: Literal["artifact_ref/v1"] = "artifact_ref/v1"
    artifact_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    kind: Literal["factor_values", "returns", "signal", "report", "diagnostics", "starting_state", "manifest"]


class EvaluationRef(ContractModel):
    schema_version: Literal["evaluation_ref/v1"] = "evaluation_ref/v1"
    evaluation_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    evaluation_hash: str = Field(pattern=SHA256_PATTERN)
    definition_hash: str = Field(pattern=SHA256_PATTERN)
    backend: Literal["local", "wq"]
    scope: Literal["selection", "final", "platform"]


class SignalRef(ContractModel):
    schema_version: Literal["signal_ref/v1"] = "signal_ref/v1"
    artifact_ref: ArtifactRef
    evaluation_ref: EvaluationRef
    session: date
    available_at: datetime

    @model_validator(mode="after")
    def check_signal(self) -> SignalRef:
        if self.artifact_ref.kind != "signal":
            raise ValueError("signal_ref requires a signal artifact")
        if self.artifact_ref.project_id != self.evaluation_ref.project_id:
            raise ValueError("signal_ref project mismatch")
        if self.available_at.utcoffset() is None:
            raise ValueError("available_at must be timezone-aware")
        return self


class StrategyRunRef(ContractModel):
    schema_version: Literal["strategy_run_ref/v1"] = "strategy_run_ref/v1"
    strategy_run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    strategy_hash: str = Field(pattern=SHA256_PATTERN)
    evaluation_hash: str = Field(pattern=SHA256_PATTERN)
    scope: Literal["selection", "final"]


class Position(ContractModel):
    security_id: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    previous_close: float = Field(gt=0)


class DividendReceivable(ContractModel):
    event_id: str = Field(min_length=1)
    security_id: str = Field(min_length=1)
    amount: float = Field(ge=0)
    pay_session: date


class SimulationConfigV1(ContractModel):
    schema_version: Literal["simulation_config/v1"] = "simulation_config/v1"
    signal_time: Literal["after_close"] = "after_close"
    field_availability: Literal["available_at_lte_signal_time"] = "available_at_lte_signal_time"
    fill_time: Literal["next_session_open"] = "next_session_open"
    fill_price: Literal["raw_open"] = "raw_open"
    valuation_price: Literal["raw_close"] = "raw_close"
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    evaluation_start_state: Literal["fresh_cash", "carry_forward"] = "fresh_cash"
    initial_cash: float = Field(default=100_000.0, ge=0)
    initial_positions: tuple[Position, ...] = ()
    initial_receivables: tuple[DividendReceivable, ...] = ()
    starting_state_ref: ArtifactRef | None = None
    starting_state_session: date | None = None
    fees_bps: float = Field(default=0.0, ge=0, le=1000)
    slippage_bps: float = Field(default=0.0, ge=0, le=1000)
    fee_basis: Literal["absolute_buy_and_sell_notional"] = "absolute_buy_and_sell_notional"
    charge_initial_entry: Literal[True] = True
    cash_rate: Literal[0.0] = 0.0
    allow_short: Literal[False] = False
    allow_leverage: Literal[False] = False
    rebalance_every_sessions: int = Field(default=1, ge=1)
    rebalance_anchor_session: date
    missing_fill_policy: Literal["block", "skip_and_hold"] = "block"
    missing_valuation_policy: Literal["block"] = "block"
    corporate_action_policy: Literal["raw_prices_cash_and_receivables"] = "raw_prices_cash_and_receivables"
    unsupported_corporate_action_policy: Literal["block"] = "block"
    benchmark_return_type: Literal["total_return"] = "total_return"
    benchmark_dividend_reinvestment: Literal["provider_documented"] = "provider_documented"
    benchmark_cost_bps: float = Field(default=0.0, ge=0)
    event_order: tuple[str, ...] = EVENT_ORDER

    @model_validator(mode="after")
    def check_initial_state(self) -> SimulationConfigV1:
        if self.event_order != EVENT_ORDER:
            raise ValueError("simulation_config/v1 event order is fixed")
        if len({position.security_id for position in self.initial_positions}) != len(self.initial_positions):
            raise ValueError("Initial positions require unique security_id")
        if len({item.event_id for item in self.initial_receivables}) != len(self.initial_receivables):
            raise ValueError("Initial receivables require unique event_id")
        if self.evaluation_start_state == "fresh_cash":
            if self.initial_positions or self.initial_receivables or self.starting_state_ref or self.starting_state_session:
                raise ValueError("fresh_cash must start without positions, receivables, or a carry-forward source")
            if self.initial_cash <= 0:
                raise ValueError("fresh_cash initial_cash must be positive")
        elif self.starting_state_ref is None or self.starting_state_session is None:
            raise ValueError("carry_forward requires a content-addressed starting_state_ref and starting_state_session")
        elif self.starting_state_ref.kind != "starting_state":
            raise ValueError("carry_forward requires a starting_state artifact")
        starting_nav = (self.initial_cash + sum(p.quantity * p.previous_close for p in self.initial_positions)
                        + sum(item.amount for item in self.initial_receivables))
        if starting_nav <= 0:
            raise ValueError("Starting NAV must be positive")
        return self


class ResearchScope(ContractModel):
    market: str = Field(min_length=1)
    asset_class: Literal["equity"] = "equity"
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    frequency: Literal["daily"] = "daily"
    universe_id: str = Field(min_length=1)
    universe_mode: Literal["fixed_cohort", "dynamic_pit"]
    universe_version: str = Field(min_length=1)
    calendar_id: str = Field(min_length=1)
    calendar_version: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    benchmark: str = Field(min_length=1)


class EvaluationWindow(ContractModel):
    start_session: date
    end_session: date
    warmup_observations: int = Field(default=1, ge=1)
    phase: Literal["selection", "final", "platform"] = "selection"
    split_id: str = Field(min_length=1)
    split_hash: str = Field(pattern=SHA256_PATTERN)
    label_horizon_sessions: int = Field(default=1, ge=1)
    purge_sessions: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def check_window(self) -> EvaluationWindow:
        if self.start_session > self.end_session:
            raise ValueError("start_session must not exceed end_session")
        if self.purge_sessions < self.label_horizon_sessions:
            raise ValueError("purge_sessions must cover the complete label horizon")
        return self


class DataInputRef(ContractModel):
    manifest_id: str | None = None
    content_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    version_status: Literal["verified", "unknown"] = "unknown"
    source: str = Field(min_length=1)
    feed: str = Field(min_length=1)
    adjustment: str = Field(min_length=1)
    asof: datetime
    remote_run_ref: str | None = None

    @model_validator(mode="after")
    def check_data_identity(self) -> DataInputRef:
        if self.asof.utcoffset() is None:
            raise ValueError("Data asof must be timezone-aware")
        if self.version_status == "verified" and (not self.manifest_id or not self.content_sha256):
            raise ValueError("Verified data requires manifest_id and content_sha256")
        return self


class EngineIdentity(ContractModel):
    engine: Literal["python", "rust", "wq"]
    version: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    semantics_version: str = SEMANTICS_VERSION
    conformance: Literal["verified", "unverified"] = "unverified"


class EvaluationConfigV1(ContractModel):
    schema_version: Literal["evaluation_config/v1"] = "evaluation_config/v1"
    backend: Literal["local", "wq"]
    scope: ResearchScope
    window: EvaluationWindow
    direction: Literal["higher_is_better", "lower_is_better"]
    neutralization: tuple[Literal["industry", "market_cap"], ...] = ()
    simulation_config: SimulationConfigV1 | None = None
    data_inputs: tuple[DataInputRef, ...] = Field(min_length=1)
    engine: EngineIdentity
    remote_settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_backend(self) -> EvaluationConfigV1:
        if self.backend == "local":
            if self.simulation_config is None:
                raise ValueError("Local evaluations require simulation_config")
            if self.engine.engine == "wq" or self.window.phase == "platform" or self.remote_settings:
                raise ValueError("Local evaluations cannot use platform scope or settings")
            if self.scope.currency != self.simulation_config.currency:
                raise ValueError("Scope and simulation currency must match")
            if (self.simulation_config.starting_state_session is not None
                    and self.simulation_config.starting_state_session >= self.window.start_session):
                raise ValueError("Carry-forward source must precede the evaluation window")
        elif self.simulation_config is not None or self.engine.engine != "wq" or self.window.phase != "platform":
            raise ValueError("WQ evaluations require platform scope/engine and separate remote settings")
        return self

    @property
    def evidence_eligibility(self) -> str:
        if self.backend == "wq":
            return "platform_only"
        if self.engine.conformance != "verified" or any(d.version_status != "verified" for d in self.data_inputs):
            return "research_only"
        return "eligible_for_validation"


class FactorDefinitionV1(ContractModel):
    schema_version: Literal["factor_definition/v1"] = "factor_definition/v1"
    expression: str = Field(min_length=1)
    language: Literal["local", "wq"] = "local"
    semantics_version: str = SEMANTICS_VERSION
    fields: tuple[FieldContract, ...] = Field(min_length=1)

    @field_validator("expression")
    @classmethod
    def reject_empty_expression(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Expression cannot be empty")
        return value

    @model_validator(mode="after")
    def check_field_names(self) -> FactorDefinitionV1:
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("Definition field names must be unique")
        return self


def canonical_json(value: Any) -> str:
    """Canonical UTF-8 JSON; reject nonfinite values rather than hash NaN tokens."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_hash(domain: str, value: Any) -> str:
    return hashlib.sha256((domain + "\n" + canonical_json(value)).encode("utf-8")).hexdigest()


def normalize_expression(expression: str) -> str:
    """Whitespace-only token normalization, not algebraic equivalence or parsing.

    Keep quoted strings intact and identifier boundaries distinct. The actual
    expression parser must still authorize syntax/operators before evaluation.
    """
    tokens = re.findall(r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[A-Za-z_]\w*|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|\*\*|>=|<=|==|!=|&&|\|\||\S)''', expression)
    return " ".join(tokens)


def definition_hash(definition: FactorDefinitionV1) -> str:
    payload = definition.model_dump(mode="json")
    payload["expression"] = normalize_expression(definition.expression)
    payload["fields"] = sorted(payload["fields"], key=lambda item: item["name"])
    return content_hash("quantgpt.definition/v1", payload)


def evaluation_hash(definition: FactorDefinitionV1, config: EvaluationConfigV1) -> str:
    if definition.language != config.backend:
        raise ValueError("Definition language and evaluation backend must match")
    if definition.semantics_version != config.engine.semantics_version:
        raise ValueError("Definition and engine semantics_version must match")
    return content_hash("quantgpt.evaluation/v1", {
        "definition_hash": definition_hash(definition), "config": config.model_dump(mode="json"),
    })


def strategy_hash(spec: Mapping[str, Any], config: EvaluationConfigV1, evaluations: Sequence[EvaluationRef]) -> str:
    """Full strategy, configuration and ordered component evidence identity."""
    _reject_live_fields(spec)
    return content_hash("quantgpt.strategy/v1", {
        "spec": dict(spec), "config": config.model_dump(mode="json"),
        "evaluations": [ref.model_dump(mode="json") for ref in evaluations],
    })


class ResearchStatus(ContractModel):
    contract_verified: bool = False
    external_integration: Literal["not_run", "verified", "blocked"] = "not_run"
    research_decision: ResearchDecision = "not_evaluated"


class PrincipalContext(ContractModel):
    """Server-created context, never populated from a caller's project_id alone."""

    actor_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    transport: Literal["http", "stdio", "worker"]
    resolved_from: Literal["authenticated_credentials", "server_connection_config", "frozen_task_actor"]

    @model_validator(mode="after")
    def check_resolution(self) -> PrincipalContext:
        expected = {"http": "authenticated_credentials", "stdio": "server_connection_config", "worker": "frozen_task_actor"}
        if self.resolved_from != expected[self.transport]:
            raise ValueError("Principal resolution must match its server-controlled transport")
        return self


class ProjectAuthorizer(Protocol):
    def require_current_membership(self, *, actor_id: str, project_id: str, permission: str) -> None: ...


def authorize_reference(principal: PrincipalContext, reference: ArtifactRef | EvaluationRef | StrategyRunRef,
                        authorizer: ProjectAuthorizer, *, permission: str = "read") -> None:
    """Recheck live membership on every artifact/cache/run access, including workers."""
    if principal.project_id != reference.project_id:
        raise PermissionError("PROJECT_SCOPE_MISMATCH")
    authorizer.require_current_membership(actor_id=principal.actor_id, project_id=reference.project_id, permission=permission)


TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancel_requested", "cancelled"}),
    "running": frozenset({"completed", "failed", "cancel_requested", "interrupted", "remote_outcome_unknown"}),
    "cancel_requested": frozenset({"cancelled", "local_wait_cancelled", "remote_cancel_confirmed", "interrupted"}),
    "interrupted": frozenset({"queued", "failed", "cancelled", "reconciliation_required"}),
    "remote_outcome_unknown": frozenset({"reconciliation_required", "cancel_requested"}),
    "reconciliation_required": frozenset({"running", "completed", "failed", "remote_cancel_confirmed"}),
    "local_wait_cancelled": frozenset({"reconciliation_required", "remote_cancel_confirmed"}),
    "completed": frozenset(), "failed": frozenset(), "cancelled": frozenset(), "remote_cancel_confirmed": frozenset(),
}


def validate_task_transition(previous: str, next_state: str) -> None:
    if next_state not in TASK_TRANSITIONS.get(previous, frozenset()):
        raise ValueError(f"INVALID_TASK_TRANSITION: {previous} -> {next_state}")
