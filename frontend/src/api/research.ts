import { authFetch, BASE, parseError } from "./client";

export interface ResearchProject {
  id: string;
  name: string;
  market: string;
  owner_user_id?: string;
}

export async function listResearchProjects(): Promise<ResearchProject[]> {
  const response = await authFetch(`${BASE}/api/v1/research/projects`);
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()).projects;
}

export async function createResearchProject(name: string, market: string): Promise<ResearchProject> {
  const response = await authFetch(`${BASE}/api/v1/research/projects`, {
    method: "POST", body: JSON.stringify({ name, market }),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export interface ResearchEvaluation {
  evaluation_id: string;
  project_id: string;
  definition_hash: string;
  evaluation_hash: string;
  backend: string;
  status: string;
  evidence_status: string;
  failure_reason?: string | null;
  summary?: { metrics?: Record<string, unknown>; artifacts?: Record<string, unknown>[]; blockers?: unknown[]; research_decision?: string };
  frozen_config?: { definition: Record<string, unknown>; config: Record<string, unknown> };
}

export interface OfflineResearchDemo {
  synthetic: true;
  notice: string;
  config: Record<string, unknown>;
  definitions: Record<string, unknown>[];
  strategy_template: Record<string, unknown>;
  field_catalog?: Record<string, unknown>[];
}

export interface ResearchRun {
  strategy_run_id: string;
  project_id: string;
  metrics: Record<string, unknown>;
  artifacts: { artifact_id: string; kind: string }[];
  evidence_status: string;
  research_decision: string;
  blockers: unknown[];
  signal_ref?: Record<string, unknown> & { artifact_id: string };
  config: Record<string, unknown>;
}

async function projectRequest<T>(projectId: string, path: string, body?: unknown): Promise<T> {
  const response = await authFetch(`${BASE}/api/v1/research/projects/${encodeURIComponent(projectId)}/${path}`, body === undefined ? {} : { method: "POST", body: JSON.stringify(body) });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export const prepareOfflineDemo = (projectId: string) => projectRequest<OfflineResearchDemo>(projectId, "offline-demo", {});
export const evaluateResearchFactors = (projectId: string, definitions: Record<string, unknown>[], config: Record<string, unknown>) => projectRequest<{ evaluations: ResearchEvaluation[] }>(projectId, "evaluations", { definitions, config });
export const runResearchStrategy = (projectId: string, spec: Record<string, unknown>, config: Record<string, unknown>) => projectRequest<ResearchRun>(projectId, "strategy-runs", { spec, config });
export const getResearchRun = (projectId: string, runId: string) => projectRequest<ResearchRun>(projectId, `strategy-runs/${encodeURIComponent(runId)}`);
export const getResearchArtifact = (projectId: string, artifactId: string) => projectRequest<{ kind: string; payload: Record<string, unknown>[] }>(projectId, `artifacts/${encodeURIComponent(artifactId)}`);
export const registerResearchHypothesis = (projectId: string, definition: Record<string, unknown>, economicHypothesis: string) => projectRequest<Record<string, unknown>>(projectId, "hypotheses", { definition, economic_hypothesis: economicHypothesis });
export const optimizeResearchSignal = (projectId: string, signalRef: Record<string, unknown>, config: Record<string, unknown>) => projectRequest<Record<string, unknown>>(projectId, "portfolio-optimizations", { signal_ref: signalRef, config });

export async function saveEvaluationToPool(projectId: string, evaluation: ResearchEvaluation, definition: Record<string, unknown>, config: Record<string, unknown>): Promise<void> {
  const scope = config.scope as Record<string, unknown>;
  const response = await authFetch(`${BASE}/api/v1/factor-pool`, { method: "POST", body: JSON.stringify({
    project_id: projectId, evaluation_id: evaluation.evaluation_id, expression: definition.expression,
    market: scope.market, universe: scope.universe_id, tags: ["favorite"], source: "web",
    name: "研究评价收藏", pool_status: "watchlist",
  }) });
  if (!response.ok) throw new Error(await parseError(response));
}

export async function getResearchEvaluation(projectId: string, evaluationId: string): Promise<ResearchEvaluation> {
  const response = await authFetch(`${BASE}/api/v1/research/projects/${encodeURIComponent(projectId)}/evaluations/${encodeURIComponent(evaluationId)}`);
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function exportResearchStrategy(projectId: string, runId: string): Promise<Record<string, unknown>> {
  const response = await authFetch(`${BASE}/api/v1/research/projects/${encodeURIComponent(projectId)}/strategy-runs/${encodeURIComponent(runId)}/export`, { method: "POST" });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}
