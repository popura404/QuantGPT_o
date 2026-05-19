import { authFetch, BASE, parseError } from "./client";
import type {
  WQAlphaListResponse,
  WQBatchFinalizePayload,
  WQBatchStatusPayload,
  WQBatchSubmitByIdPayload,
  WQBatchSubmitPayload,
  WQBrainSimulationPayload,
  WQBrainStatus,
  WQSubmitResponse,
  WQTaskResponse,
} from "../types/wqBrain";

export class WQBrainApiError extends Error {
  detail: unknown;

  constructor(message: string, detail: unknown) {
    super(message);
    this.name = "WQBrainApiError";
    this.detail = detail;
  }
}

function withQuery(path: string, params: Record<string, string | number | null | undefined>) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  });
  const qs = search.toString();
  return `${BASE}${path}${qs ? `?${qs}` : ""}`;
}

async function throwWQError(res: Response): Promise<never> {
  try {
    const body = await res.json();
    const detail = body.detail;
    const message = typeof detail === "string"
      ? detail
      : detail && typeof detail === "object" && "message" in detail
        ? String((detail as { message?: unknown }).message)
        : `请求失败 (${res.status})`;
    throw new WQBrainApiError(message, detail);
  } catch (err) {
    if (err instanceof WQBrainApiError) throw err;
    throw new Error(await parseError(res));
  }
}

export async function getWQBrainStatus(): Promise<WQBrainStatus> {
  const res = await authFetch(`${BASE}/api/v1/wq-brain/status`);
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function getWQBrainUserInfo(account = "primary"): Promise<Record<string, unknown>> {
  const res = await authFetch(withQuery("/api/v1/wq-brain/user-info", { account }));
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function submitWQBrainSimulation(payload: WQBrainSimulationPayload): Promise<WQTaskResponse> {
  const res = await authFetch(`${BASE}/api/v1/wq-brain/submit`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function listWQPlatformAlphas(account = "primary", limit = 100, offset = 0): Promise<WQAlphaListResponse> {
  const res = await authFetch(withQuery("/api/v1/wq-brain/platform-alphas", { account, limit, offset }));
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function listSubmittedAlphas(limit = 50, offset = 0): Promise<WQAlphaListResponse> {
  const res = await authFetch(withQuery("/api/v1/wq-brain/submitted-alphas", { limit, offset }));
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function submitAlphaFromTask(taskId: string, submissionOverrideReason?: string | null): Promise<WQSubmitResponse> {
  const res = await authFetch(withQuery(`/api/v1/wq-brain/${taskId}/submit-alpha`, {
    submission_override_reason: submissionOverrideReason,
  }), { method: "POST" });
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function checkAlphaStatus(alphaId: string, account = "primary"): Promise<Record<string, unknown>> {
  const res = await authFetch(withQuery(`/api/v1/wq-brain/alpha-status/${alphaId}`, { account }));
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function submitAlphaById(
  alphaId: string,
  account = "primary",
  expression?: string | null,
  submissionOverrideReason?: string | null,
): Promise<WQSubmitResponse> {
  const res = await authFetch(withQuery(`/api/v1/wq-brain/submit-by-id/${alphaId}`, {
    account,
    expression,
    submission_override_reason: submissionOverrideReason,
  }), { method: "POST" });
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function deleteAlpha(alphaId: string, account = "primary"): Promise<Record<string, unknown>> {
  const res = await authFetch(withQuery(`/api/v1/wq-brain/alpha/${alphaId}`, { account }), { method: "DELETE" });
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function unhideAlpha(alphaId: string, account = "primary"): Promise<Record<string, unknown>> {
  const res = await authFetch(withQuery(`/api/v1/wq-brain/alpha/${alphaId}/unhide`, { account }), { method: "POST" });
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function submitWQBatch(payload: WQBatchSubmitPayload): Promise<WQTaskResponse> {
  const res = await authFetch(`${BASE}/api/v1/wq-brain/batch-submit`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function submitWQBatchById(payload: WQBatchSubmitByIdPayload): Promise<WQTaskResponse> {
  const res = await authFetch(`${BASE}/api/v1/wq-brain/batch-submit-by-id`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function checkWQBatchAlphaStatus(payload: WQBatchStatusPayload, account = "primary"): Promise<Record<string, unknown>> {
  const res = await authFetch(withQuery("/api/v1/wq-brain/batch-alpha-status", { account }), {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) return throwWQError(res);
  return res.json();
}

export async function finalizeWQBatch(payload: WQBatchFinalizePayload): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/wq-brain/batch-finalize`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
