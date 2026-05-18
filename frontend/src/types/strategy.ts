export type StrategySpec = Record<string, unknown>;

export interface StrategyTemplateSummary {
  id: string;
  name: string;
  description: string;
  risk_label: string;
  parameter_bounds: Record<string, unknown>;
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
  action_hint: string;
  constraint_reasons: string[];
  notice: string;
}

export interface StrategyExportPayload {
  strategy_name: string;
  spec_version: string;
  market: string;
  data_end: string;
  notice: string;
  signals: StrategyExportSignal[];
  json_path?: string;
  csv_path?: string;
}
