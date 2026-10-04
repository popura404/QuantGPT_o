import type { BacktestMetrics } from "../types/backtest";
import { authFetch, BASE, parseError } from "./client";
import { getResearchProjectId } from "../hooks/useResearchProject";

export interface SavedFactor {
  id: string;
  task_id: string | null;
  expression: string;
  name: string | null;
  note: string | null;
  tags: string[];
  metrics: BacktestMetrics | null;
  backtest_summary: Record<string, unknown> | null;
  params: Record<string, unknown> | null;
  report_url: string | null;
  created_at: string | null;
  project_id?: string | null;
  definition_hash?: string | null;
  evaluation_id?: string | null;
  evidence_status?: string;
  pool_status?: string;
  market?: string;
  main_reason?: string | null;
}

export interface SaveFactorPayload {
  task_id?: string;
  expression: string;
  name?: string;
  note?: string;
  tags?: string[];
  metrics?: Record<string, unknown>;
  backtest_summary?: Record<string, unknown>;
  params?: Record<string, unknown>;
  report_url?: string;
}

export async function saveFactor(payload: SaveFactorPayload): Promise<SavedFactor> {
  const projectId = getResearchProjectId();
  const data = projectId ? { ...payload, project_id: projectId, tags: [...(payload.tags ?? []), "favorite"], source: "web", market: payload.params?.market ?? "a_share" } : payload;
  const res = await authFetch(`${BASE}/api/v1/${projectId ? "factor-pool" : "factor-library"}`, {
    method: "POST",
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  const result = await res.json();
  return projectId ? result.entry : result;
}

export async function fetchFactors(projectId = getResearchProjectId()): Promise<SavedFactor[]> {
  const url = projectId ? `${BASE}/api/v1/factor-pool?project_id=${encodeURIComponent(projectId)}&limit=200` : `${BASE}/api/v1/factor-library`;
  const res = await authFetch(url);
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return projectId ? data.entries : data.factors;
}

export async function updateFactor(
  factorId: string,
  updates: { name?: string; note?: string; tags?: string[] },
  projectId = getResearchProjectId(),
): Promise<SavedFactor> {
  const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  const res = await authFetch(`${BASE}/api/v1/${projectId ? "factor-pool" : "factor-library"}/${encodeURIComponent(factorId)}${suffix}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteFactor(factorId: string, projectId = getResearchProjectId()): Promise<void> {
  const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  const res = await authFetch(`${BASE}/api/v1/${projectId ? "factor-pool" : "factor-library"}/${encodeURIComponent(factorId)}${suffix}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) throw new Error(await parseError(res));
}
