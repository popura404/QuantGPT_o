import type { StrategyBacktestTaskResult } from "./strategy";

export type TaskStatus =
  | "pending"
  | "queued"
  | "running"
  | "authenticating"
  | "generating_expression"
  | "validating"
  | "fetching_data"
  | "checking_data_quality"
  | "fetching_fundamentals"
  | "backtesting"
  | "simulating"
  | "submitted"
  | "finalizing"
  | "analyzing"
  | "generating_report"
  | "completed"
  | "failed"
  | "cancelled"
  | "iterating"
  | "iteration_completed"
  | (string & {});

export type TaskType =
  | "backtest"
  | "iteration"
  | "composite"
  | "strategy_backtest"
  | "wq_brain_submit"
  | "wq_brain_batch"
  | "wq_brain_batch_submit_by_id"
  | "wq_brain_finalize"
  | (string & {});

export type DirectionMode = "auto_full" | "fixed";
export type ValidationStage = "selection" | "final";

export interface OOSRequest {
  method?: "date_ratio" | "date_cut";
  train_ratio?: number;
  valid_ratio?: number;
  test_ratio?: number;
  train_end?: string | null;
  valid_end?: string | null;
  min_train_days?: number;
  min_valid_days?: number;
  min_test_days?: number;
  warmup_days?: number | null;
}

export interface DataQualityRequest {
  enabled?: boolean;
  mode?: "report_only" | "filter" | "strict";
  min_price?: number;
  max_abs_daily_ret?: number;
  max_missing_ratio_per_stock?: number;
  require_positive_volume?: boolean;
  require_positive_amount?: boolean;
  drop_st?: boolean;
  drop_new_listing_days?: number;
  adjustment?: "qfq" | "hfq" | "none" | "unknown";
  fail_on_unknown_adjustment?: boolean;
}

export interface BacktestRequest {
  prompt: string;
  universe?: string;
  universe_date?: string | null;
  start_date?: string;
  end_date?: string;
  n_groups?: number;
  holding_period?: number;
  benchmark?: string;
  neutralize_industry?: boolean;
  neutralize_cap?: boolean;
  rebalance_anchor?: string | null;
  oos_enabled?: boolean;
  oos?: OOSRequest | null;
  validation_stage?: ValidationStage;
  direction_mode?: DirectionMode;
  fixed_direction?: 1 | -1 | null;
  data_quality?: DataQualityRequest | null;
}

export interface BacktestMetrics {
  total_return: number;
  cagr: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  volatility: number;
  win_rate: number;
  profit_factor: number;
  benchmark_total_return?: number | null;
  benchmark_cagr?: number | null;
}

export interface GroupReturn {
  group: string;
  annual_return: number;
  sharpe: number;
  max_drawdown: number;
}

export interface IterationCandidate {
  expression: string;
  score: number;
  grade: "A" | "B" | "C" | "D";
  component_scores: Record<string, number>;
  backtest_summary: {
    long_short_sharpe: number;
    long_short_annual?: number;
    top_group_sharpe?: number;
    monotonicity_score: number;
    spread: number;
    group_returns: Record<string, GroupReturn>;
    ic_mean?: number;
    rank_ic_mean?: number;
    ic_ir?: number;
    ic_win_rate?: number;
    turnover?: number;
    wq_fitness?: number;
  };
  report_metrics: BacktestMetrics;
  report_url: string;
  status: "success" | "failed";
  error?: string;
}

export interface StockFactorInfo {
  stock_code: string;
  factor_value: number;
  factor_rank: number;
  group: number;
  group_label: string;
  period_return: number;
}

export interface StockFactorData {
  rebalance_date: string;
  flipped: boolean;
  total_stock_count: number;
  stocks: StockFactorInfo[];
}

export interface FactorInterpretation {
  logic: string;
  source: string;
  guidance: string;
  risk: string;
  rating?: string;
  rating_reason?: string;
  conclusion?: string;
  suggestions?: string[];
}

export interface WQISTest {
  value: number;
  threshold?: number;
  threshold_min?: number;
  threshold_max?: number;
  label: string;
  pass: boolean;
}

export interface WQSubUniverse {
  sub_sharpe_a: number;
  sub_sharpe_b: number;
  sub_sharpe_min: number;
  threshold: number;
  pass: boolean;
}

export interface WQBrain {
  wq_sharpe: number;
  wq_turnover: number;
  wq_returns: number;
  wq_fitness: number;
  wq_max_weight: number;
  wq_rating: string;
  margin_bps: number;
  submittable: boolean;
  sub_universe: WQSubUniverse;
  wq_is_tests: Record<string, WQISTest>;
}

export interface SubmissionPreflight {
  allowed?: boolean;
  reasons?: string[];
  warnings?: string[];
  override_reason?: string | null;
  [key: string]: unknown;
}

export interface WQBrainTaskResult extends Partial<BacktestResult> {
  ok?: boolean;
  alpha_id?: string;
  expression?: string;
  submitted?: boolean;
  submit_state?: string;
  status?: string;
  final_status?: string;
  platform_status?: string;
  detail?: string;
  error?: string;
  is_metrics?: Record<string, number | string | null>;
  oos_metrics?: Record<string, number | string | null>;
  submission_preflight?: SubmissionPreflight;
  alphas?: Record<string, unknown> | Record<string, unknown>[];
  summary?: Record<string, unknown>;
  sub_results?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface BacktestResult {
  report_url: string;
  metrics: BacktestMetrics;
  wq_brain?: WQBrain;
  backtest_summary: {
    long_short_sharpe: number;
    long_short_annual?: number;
    top_group_sharpe?: number;
    monotonicity_score: number;
    spread: number;
    group_returns: Record<string, GroupReturn>;
    ic_mean?: number;
    rank_ic_mean?: number;
    ic_ir?: number;
    ic_win_rate?: number;
    turnover?: number;
    wq_fitness?: number;
  };
  params: {
    expression: string;
    universe: string;
    universe_date?: string | null;
    rebalance_anchor?: string | null;
    start_date: string;
    end_date: string;
    n_groups: number;
    holding_period: number;
    benchmark: string;
    stock_count: number;
    oos_enabled?: boolean;
    oos?: OOSRequest | null;
    validation_stage?: ValidationStage;
    direction_mode?: DirectionMode;
    fixed_direction?: 1 | -1 | null;
    data_quality?: DataQualityRequest | null;
  };
  llm: {
    prompt: string;
    generated_expression: string;
  };
  interpretation?: FactorInterpretation;
  stock_factor_data?: StockFactorData | null;
  nav_series?: { date: string; value: number }[];
  data_quality?: Record<string, unknown>;
  direction_policy?: string;
  report_scope?: string;
  oos_result?: {
    direction_policy?: string;
    fixed_direction?: number;
    report_scope?: string;
    train?: { period?: string[]; metrics?: Record<string, number | string | null> };
    valid?: { period?: string[]; metrics?: Record<string, number | string | null> };
    test?: { period?: string[]; metrics?: Record<string, number | string | null> };
    decay?: Record<string, number | null>;
    oos_risk?: string;
    data_quality?: Record<string, unknown>;
    warnings?: string[];
  };
  scoring?: {
    score?: number;
    grade?: string;
    decision?: string;
    overfit_risk?: string;
    train_score?: number;
    valid_score?: number;
    test_score?: number;
    reasons?: string[];
    metrics_scope?: string;
  };
}

export interface Session {
  id: string;
  name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Task {
  task_id: string;
  status: TaskStatus;
  session_id?: string;
  params?: BacktestRequest;
  expression?: string;
  error?: string;
  result?: BacktestResult | (StrategyBacktestTaskResult & Partial<BacktestResult>) | WQBrainTaskResult;
  task_type?: TaskType;
  parent_task_id?: string;
  candidates?: IterationCandidate[];
  candidates_done?: number;
  candidates_total?: number;
  selected_candidate_index?: number;
  progress?: number;
  progress_message?: string;
  completed?: number;
  completed_combinations?: number;
  sub_results?: Record<string, unknown>;
  created_at?: string;
  completed_at?: string;
  duration_seconds?: number;
}
