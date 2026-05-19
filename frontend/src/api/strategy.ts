import { authFetch, BASE, parseError } from "./client";
import type {
  StrategyBacktestPayload,
  StrategyBacktestResultPayload,
  StrategyExportPayload,
  StrategyRunRecord,
  StrategySpecRecord,
  StrategyTemplateSummary,
  StrategyValidationResult,
} from "../types/strategy";

export async function listStrategyTemplates(): Promise<StrategyTemplateSummary[]> {
  const res = await authFetch(`${BASE}/api/v1/strategy/templates`);
  if (!res.ok) throw new Error(await parseError(res));
  const body = await res.json();
  return body.templates;
}

export async function instantiateStrategyTemplate(templateId: string): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/strategy/templates/${templateId}/instantiate`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(await parseError(res));
  const body = await res.json();
  return body.spec;
}

export async function listStrategyMarkets(): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/strategy/markets`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listStrategyDataFields(market = "a_share"): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/strategy/data-fields?market=${encodeURIComponent(market)}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getStrategyTemplate(templateId: string): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/strategy/templates/${encodeURIComponent(templateId)}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function validateStrategySpec(spec: Record<string, unknown>): Promise<StrategyValidationResult> {
  const res = await authFetch(`${BASE}/api/v1/strategy/validate`, {
    method: "POST",
    body: JSON.stringify({ spec }),
  });
  if (!res.ok) {
    try {
      const body = await res.json();
      if (body.detail && typeof body.detail === "object") return body.detail as StrategyValidationResult;
    } catch {
      // fall through
    }
    throw new Error(await parseError(res));
  }
  return res.json();
}

export async function submitStrategyBacktest(payload: StrategyBacktestPayload): Promise<{ task_id: string; status: string }> {
  const res = await authFetch(`${BASE}/api/v1/strategy/backtest`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function exportStrategyCandidate(result: StrategyBacktestResultPayload): Promise<StrategyExportPayload> {
  const res = await authFetch(`${BASE}/api/v1/strategy/export`, {
    method: "POST",
    body: JSON.stringify({ result }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function diagnoseStrategy(result: StrategyBacktestResultPayload): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/strategy/diagnose`, {
    method: "POST",
    body: JSON.stringify({ result }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function runStrategyAntiOverfit(result: StrategyBacktestResultPayload): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/strategy/anti-overfit`, {
    method: "POST",
    body: JSON.stringify({ result }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function runStrategyRollingValidation(
  result: StrategyBacktestResultPayload,
  windows = 3,
): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/strategy/rolling-validation`, {
    method: "POST",
    body: JSON.stringify({ result, windows }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function optimizeStrategyCandidate(
  signals: Record<string, unknown>[],
  spec: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/strategy/optimize`, {
    method: "POST",
    body: JSON.stringify({ signals, spec }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function saveStrategySpec(
  spec: Record<string, unknown>,
  name?: string | null,
  tags: string[] = [],
): Promise<StrategySpecRecord> {
  const res = await authFetch(`${BASE}/api/v1/strategy/specs`, {
    method: "POST",
    body: JSON.stringify({ spec, name, tags }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listStrategySpecs(): Promise<StrategySpecRecord[]> {
  const res = await authFetch(`${BASE}/api/v1/strategy/specs`);
  if (!res.ok) throw new Error(await parseError(res));
  const body = await res.json();
  return body.strategies ?? [];
}

export async function getStrategySpec(strategyId: string): Promise<StrategySpecRecord> {
  const res = await authFetch(`${BASE}/api/v1/strategy/specs/${encodeURIComponent(strategyId)}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function saveStrategyRun(
  result: StrategyBacktestResultPayload,
  strategyId?: string | null,
  taskId?: string | null,
  reportUrl?: string | null,
  summaryJson?: string | null,
  signalExport?: StrategyExportPayload | null,
): Promise<StrategyRunRecord> {
  const res = await authFetch(`${BASE}/api/v1/strategy/runs`, {
    method: "POST",
    body: JSON.stringify({
      result,
      strategy_id: strategyId,
      task_id: taskId,
      report_url: reportUrl,
      summary_json: summaryJson,
      signal_export: signalExport,
    }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listStrategyRuns(strategyId?: string | null): Promise<StrategyRunRecord[]> {
  const suffix = strategyId ? `?strategy_id=${encodeURIComponent(strategyId)}` : "";
  const res = await authFetch(`${BASE}/api/v1/strategy/runs${suffix}`);
  if (!res.ok) throw new Error(await parseError(res));
  const body = await res.json();
  return body.runs ?? [];
}
