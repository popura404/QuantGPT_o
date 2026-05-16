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
