export type StrategySpec = Record<string, unknown>;

export interface StrategyTemplateSummary {
  id: string;
  name: string;
  description: string;
  risk_label: string;
  parameter_bounds: Record<string, unknown>;
}

export interface StrategySpecRecord {
  id: string;
  name: string;
  schema_version: string;
  market: string;
  universe: string;
  spec: StrategySpec;
  tags: string[];
  status?: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface StrategyRunRecord {
  id: string;
  strategy_id?: string | null;
  task_id?: string | null;
  result: StrategyBacktestResultPayload;
  report_url?: string | null;
  summary_json?: string | null;
  signal_export?: StrategyExportPayload | null;
  created_at?: string | null;
}

export interface StrategyValidationIssue {
  code: string;
  message: string;
  path?: string;
  hint?: string;
}

export interface StrategyValidationResult {
  is_valid: boolean;
  issues: StrategyValidationIssue[];
  spec?: StrategySpec;
}

export interface StrategyBacktestPayload {
  spec: StrategySpec;
  start_date: string;
  end_date: string;
  benchmark?: string;
}

export interface StrategyScore {
  score: number;
  grade: "A" | "B" | "C" | "D" | string;
  decision?: "candidate" | "watchlist" | "reject" | string;
  overfit_risk?: string;
  train_score?: number;
  valid_score?: number;
  test_score?: number;
  stability_score?: number;
  decay_penalty?: number;
  data_quality_penalty?: number;
  reasons?: string[];
  metrics_scope?: string;
  components?: Record<string, number>;
  failure_reasons?: string[];
  risk_logs?: Record<string, unknown>[];
  validation_issues?: StrategyValidationIssue[];
  strategy_anti_overfit?: string;
  strategy_rolling_validation?: string;
  strategy_diagnosis?: Record<string, unknown>;
}

export interface StrategyHolding {
  stock_code: string;
  target_weight?: number;
  weight?: number;
  score?: number;
  trade_date?: string;
  [key: string]: unknown;
}

export interface StrategyBacktestResultPayload {
  spec?: StrategySpec;
  spec_version?: string;
  strategy_name?: string;
  market?: string;
  universe?: string;
  start_date?: string;
  end_date?: string;
  benchmark?: string;
  data_end?: string;
  metrics?: Record<string, number | string | null>;
  latest_holdings?: StrategyHolding[];
  risk_logs?: Record<string, unknown>[];
  validation_issues?: StrategyValidationIssue[];
  diagnostics?: Record<string, unknown>;
  validation_mode?: string;
  direction_policy?: string;
  data_quality?: Record<string, unknown>;
  data_snapshot_id?: string;
  data_source?: string;
  data_source_metadata?: Record<string, unknown>;
  experiment_id?: string;
  factor_hash?: string;
  config_hash?: string;
  promotion_state?: string;
  promotion_blockers?: string[];
  validation_provenance?: Record<string, unknown>;
  oos_summary?: Record<string, unknown>;
  oos_score?: StrategyScore;
  oos_result?: {
    validation_mode?: string;
    direction_policy?: string;
    direction_source?: string;
    rebalance_anchor?: string;
    resolved_warmup_days?: number;
    train?: { period?: string[]; metrics?: Record<string, number | string | null> };
    valid?: { period?: string[]; metrics?: Record<string, number | string | null> };
    test?: { period?: string[]; metrics?: Record<string, number | string | null> };
    decay?: Record<string, number | null>;
    warnings?: string[];
    data_snapshot_id?: string;
    data_source?: string;
    data_quality?: Record<string, unknown>;
  };
  strategy_returns?: Record<string, unknown>[];
  target_weights?: Record<string, unknown>[];
  cash_weights?: Record<string, unknown>[];
  turnover_by_rebalance?: Record<string, unknown>[];
  cost_by_rebalance?: Record<string, unknown>[];
  non_live_trading_notice?: string;
}

export interface StrategyBacktestTaskResult {
  strategy_result?: StrategyBacktestResultPayload;
  strategy_score?: StrategyScore;
  summary_json?: string;
  report_url?: string;
}

export interface StrategyExportSignal {
  trade_date: string;
  stock_code: string;
  target_weight: number;
  rank?: number;
  constraint_reasons: string[];
  notice: string;
}

export interface StrategyExportPayload {
  schema_version?: "strategy_signal.v1" | string;
  strategy_id?: string;
  strategy_name: string;
  strategy_version?: string;
  spec_version: string;
  experiment_id?: string;
  factor_hash?: string;
  created_at?: string;
  as_of?: string;
  market: string;
  asset_class?: string;
  universe?: string;
  rebalance_frequency?: string;
  holding_period?: number;
  signal_type?: string;
  data_end: string;
  notice: string;
  validation_summary?: Record<string, unknown>;
  risk_constraints?: Record<string, unknown>;
  validation_provenance?: Record<string, unknown>;
  signals: StrategyExportSignal[];
  json_path?: string;
  csv_path?: string;
}
