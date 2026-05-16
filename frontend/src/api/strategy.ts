import { authFetch, BASE, parseError } from "./client";
import type { StrategyBacktestPayload, StrategyTemplateSummary, StrategyValidationResult } from "../types/strategy";

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
