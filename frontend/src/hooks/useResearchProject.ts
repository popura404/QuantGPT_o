import { useSyncExternalStore } from "react";

const KEY = "quantgpt_research_project";
const EVENT = "quantgpt-research-project-change";

export function getResearchProjectId(): string | null {
  return localStorage.getItem(KEY);
}

export function setResearchProjectId(projectId: string | null): void {
  if (projectId) localStorage.setItem(KEY, projectId);
  else localStorage.removeItem(KEY);
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(callback: () => void): () => void {
  window.addEventListener(EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

export function useResearchProject(): string | null {
  return useSyncExternalStore(subscribe, getResearchProjectId, () => null);
}
