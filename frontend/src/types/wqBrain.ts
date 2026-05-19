import type { SubmissionPreflight, Task } from "./backtest";

export type WQAccount = "primary" | "alt";

export interface WQBrainStatus {
  configured: boolean;
  accounts: string[];
  thresholds: Record<string, unknown>;
}

export interface WQBrainSimulationPayload {
  expression: string;
  tag: string;
  region: string;
  universe: string;
  delay: number;
  decay: number;
  neutralization: string;
  truncation: number;
  account: WQAccount;
  auto_submit: boolean;
  submission_override_reason?: string | null;
  session_id?: string | null;
}

export interface WQPlatformAlpha {
  alpha_id?: string;
  id?: string;
  expression?: string;
  status?: string;
  grade?: string;
  sharpe?: number;
  fitness?: number;
  returns?: number;
  turnover?: number;
  [key: string]: unknown;
}

export interface WQAlphaListResponse {
  total: number;
  alphas: WQPlatformAlpha[];
}

export interface WQSubmitResponse {
  alpha_id?: string;
  submitted?: boolean;
  ok?: boolean;
  detail?: string;
  error?: string;
  submission_preflight?: SubmissionPreflight;
  [key: string]: unknown;
}

export interface WQBatchSubmitPayload {
  expression: string;
  tag: string;
  regions: string[];
  delays: number[];
  universes: string[];
  neutralizations: string[];
  decay: number;
  truncation: number;
  account: WQAccount;
  auto_submit: boolean;
  submission_override_reason?: string | null;
  session_id?: string | null;
}

export interface WQBatchSubmitByIdPayload {
  alpha_ids: string[];
  account: WQAccount;
  expressions_by_alpha_id?: Record<string, string> | null;
  submission_override_reason?: string | null;
}

export interface WQBatchStatusPayload {
  alpha_ids: string[];
}

export interface WQBatchFinalizePayload {
  alpha_ids: string[];
  account: WQAccount;
}

export interface WQTaskResponse {
  task_id: string;
  status: Task["status"];
  total?: number;
  total_combinations?: number;
}
