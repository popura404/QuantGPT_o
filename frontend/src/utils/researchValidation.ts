import type { ResearchEvaluation } from "../api/research";

export function canValidateLocally(evaluation: ResearchEvaluation): boolean {
  return evaluation.backend === "local" && evaluation.summary?.performance_observed === true;
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
  return JSON.stringify(value) ?? "null";
}

function candidateConfig(evaluation: ResearchEvaluation): string | null {
  const config = evaluation.frozen_config?.config;
  if (!config) return null;
  const window = { ...(config.window as Record<string, unknown>) };
  delete window.start_session; delete window.end_session; delete window.phase;
  return canonical({ ...config, window });
}

/** Only link a selection already present with the same frozen candidate and inputs. */
export function matchingSelectionEvaluation(evaluation: ResearchEvaluation, available: ResearchEvaluation[]): string | undefined {
  const window = evaluation.frozen_config?.config.window as Record<string, unknown> | undefined;
  if (window?.phase !== "final" || typeof window.start_session !== "string") return;
  const identity = candidateConfig(evaluation);
  const matches = available.filter((candidate) => {
    const before = candidate.frozen_config?.config.window as Record<string, unknown> | undefined;
    return candidate.project_id === evaluation.project_id && candidate.definition_hash === evaluation.definition_hash
      && canValidateLocally(candidate) && before?.phase === "selection" && typeof before.end_session === "string"
      && before.end_session < String(window.start_session) && candidateConfig(candidate) === identity;
  });
  // More than one matching selection needs explicit provenance; do not guess.
  return matches.length === 1 ? matches[0].evaluation_id : undefined;
}
