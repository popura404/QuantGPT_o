import type { BacktestRequest, Task, Session } from "../types/backtest";

export const BASE = "";

let _authDisabled = false;
export function setAuthDisabled(v: boolean) { _authDisabled = v; }
export function getAuthDisabled() { return _authDisabled; }

function getAccessToken(): string | null {
  return localStorage.getItem("quantgpt_access_token");
}

function authHeaders(): Record<string, string> {
  if (_authDisabled) return { "Content-Type": "application/json" };
  const token = getAccessToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

export async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const headers = { ...authHeaders(), ...options.headers };
  const res = await fetch(url, { ...options, headers });

  if (res.status === 401 && !_authDisabled) {
    // Guest tokens don't need refresh
    if (localStorage.getItem("quantgpt_is_guest") === "1") return res;

    // Try refresh
    const refreshTokenStr = localStorage.getItem("quantgpt_refresh_token");
    if (refreshTokenStr) {
      try {
        const refreshRes = await fetch(`${BASE}/api/v1/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshTokenStr }),
        });
        if (refreshRes.ok) {
          const { access_token } = await refreshRes.json();
          localStorage.setItem("quantgpt_access_token", access_token);
          // Retry original request
          const retryHeaders = { ...options.headers, "Content-Type": "application/json", Authorization: `Bearer ${access_token}` };
          return fetch(url, { ...options, headers: retryHeaders });
        }
      } catch { /* fall through */ }
    }
    // Refresh failed, redirect to login
    localStorage.removeItem("quantgpt_access_token");
    localStorage.removeItem("quantgpt_refresh_token");
    window.location.href = "/login";
  }

  return res;
}

export async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join("; ");
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    return `请求失败 (${res.status})`;
  } catch {
    if (res.status === 429) return "请求过于频繁，请稍后再试";
    if (res.status === 503) return "服务繁忙，请稍后再试";
    return `请求失败 (${res.status})`;
  }
}

export class TaskFetchError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "TaskFetchError";
    this.status = status;
  }
}

export async function submitBacktest(req: BacktestRequest, sessionId?: string): Promise<{ task_id: string; status: string }> {
  const body = sessionId ? { ...req, session_id: sessionId } : req;
  const res = await authFetch(`${BASE}/api/v1/auto_backtest`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function cancelTask(taskId: string): Promise<void> {
  const res = await authFetch(`${BASE}/api/v1/tasks/${taskId}/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res));
}

export async function getTask(taskId: string): Promise<Task> {
  const res = await authFetch(`${BASE}/api/v1/tasks/${taskId}`);
  if (!res.ok) throw new TaskFetchError(res.status, await parseError(res));
  return res.json();
}

export function streamTask(
  taskId: string,
  onUpdate: (task: Task) => void,
  onDone: () => void,
  onError?: (message: string) => void,
): () => void {
  let closed = false;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let retryCount = 0;
  let lastStreamError: string | null = null;
  let lastPollingError: string | null = null;
  const MAX_RETRIES = 3;

  function cleanup() {
    closed = true;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function startPolling(context?: string) {
    if (closed || pollTimer) return;
    if (context) lastStreamError = context;
    pollTimer = setInterval(async () => {
      if (closed) { cleanup(); return; }
      try {
        const task = await getTask(taskId);
        lastPollingError = null;
        onUpdate(task);
        if (task.status === "completed" || task.status === "failed" || task.status === "cancelled" || task.status === "iteration_completed") {
          cleanup();
          onDone();
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "任务状态读取失败";
        if (err instanceof TaskFetchError && [401, 403, 404].includes(err.status)) {
          cleanup();
          onError?.(message);
          return;
        }
        if (message !== lastPollingError) {
          lastPollingError = message;
          onError?.(message);
        }
      }
    }, 3000);
  }

  async function acquireTicket(): Promise<{ ticket: string | null; error?: string; fatal?: boolean }> {
    if (_authDisabled) return { ticket: null };
    try {
      const res = await authFetch(`${BASE}/api/v1/tasks/${taskId}/sse-ticket`, {
        method: "POST",
      });
      if (!res.ok) {
        return {
          ticket: null,
          error: await parseError(res),
          fatal: [401, 403, 404].includes(res.status),
        };
      }
      const data = await res.json();
      return { ticket: data.ticket as string };
    } catch (err) {
      return {
        ticket: null,
        error: err instanceof Error ? err.message : "SSE ticket 获取失败",
      };
    }
  }

  async function connect() {
    if (closed) return;

    const { ticket, error: ticketError, fatal } = await acquireTicket();
    if (ticketError) {
      lastStreamError = ticketError;
      if (fatal) {
        cleanup();
        onError?.(ticketError);
        return;
      }
      startPolling(ticketError);
      return;
    }
    const url = `${BASE}/api/v1/tasks/${taskId}/stream${ticket ? `?ticket=${ticket}` : ""}`;

    const es = new EventSource(url);

    es.addEventListener("update", (e) => {
      retryCount = 0;
      const task: Task = JSON.parse(e.data);
      onUpdate(task);
    });

    es.addEventListener("done", () => {
      es.close();
      cleanup();
      onDone();
    });

    es.addEventListener("error", () => {
      es.close();
      if (closed) return;
      retryCount++;
      lastStreamError = "任务流连接失败";
      if (retryCount <= MAX_RETRIES) {
        // Retry SSE after a short delay (need new ticket each time)
        setTimeout(connect, 2000 * retryCount);
      } else {
        // Fall back to polling
        startPolling(lastStreamError);
      }
    });

    // Store close function
    closeFn = () => { es.close(); cleanup(); };
  }

  let closeFn = () => { cleanup(); };
  connect();

  return () => closeFn();
}

export function getReportUrl(reportUrl: string): string {
  const url = /^https?:\/\//i.test(reportUrl) ? reportUrl : `${BASE}${reportUrl}`;
  try {
    const resolved = new URL(url, window.location.origin);
    if (resolved.origin !== window.location.origin) return url;
  } catch {
    return url;
  }
  return url;
}

function appendQueryParam(url: string, key: string, value: string): string {
  const hashIndex = url.indexOf("#");
  const baseUrl = hashIndex >= 0 ? url.slice(0, hashIndex) : url;
  const hash = hashIndex >= 0 ? url.slice(hashIndex) : "";
  const sep = baseUrl.includes("?") ? "&" : "?";
  return `${baseUrl}${sep}${key}=${encodeURIComponent(value)}${hash}`;
}

function getReportFilename(reportUrl: string): string | null {
  try {
    const resolved = new URL(getReportUrl(reportUrl), window.location.origin);
    if (resolved.origin !== window.location.origin) return null;
    const match = resolved.pathname.match(/^\/api\/v1\/reports\/([^/]+)$/);
    return match ? decodeURIComponent(match[1]) : null;
  } catch {
    return null;
  }
}

export async function createReportUrl(reportUrl: string): Promise<string> {
  const url = getReportUrl(reportUrl);
  const filename = getReportFilename(reportUrl);
  if (!filename || _authDisabled) return url;

  const res = await authFetch(`${BASE}/api/v1/reports/${encodeURIComponent(filename)}/ticket`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return appendQueryParam(url, "ticket", data.ticket as string);
}

export async function fetchTasks(
  page = 1,
  pageSize = 20,
  sessionId?: string,
  taskType?: string,
  status?: string,
): Promise<{ tasks: Task[]; page: number; page_size: number; total?: number }> {
  let url = `${BASE}/api/v1/tasks?page=${page}&page_size=${pageSize}`;
  if (sessionId) url += `&session_id=${sessionId}`;
  if (taskType) url += `&task_type=${taskType}`;
  if (status) url += `&status=${status}`;
  const res = await authFetch(url);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function submitIteration(
  taskId: string,
  nCandidates = 5,
  direction?: string,
): Promise<{ task_id: string; status: string }> {
  const body: Record<string, unknown> = { n_candidates: nCandidates };
  if (direction) body.direction = direction;
  const res = await authFetch(`${BASE}/api/v1/tasks/${taskId}/iterate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function selectCandidate(
  taskId: string,
  candidateIndex: number,
): Promise<Record<string, unknown>> {
  const res = await authFetch(`${BASE}/api/v1/tasks/${taskId}/select_candidate`, {
    method: "POST",
    body: JSON.stringify({ candidate_index: candidateIndex }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

// ---- Sessions ----

export async function createSession(name?: string): Promise<Session> {
  const res = await authFetch(`${BASE}/api/v1/sessions`, {
    method: "POST",
    body: JSON.stringify({ name: name ?? null }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function fetchSessions(): Promise<{ sessions: Session[] }> {
  const url = `${BASE}/api/v1/sessions`;
  const res = await authFetch(url);
  if (!res.ok) throw new Error(`Sessions fetch failed: ${res.status}`);
  return res.json();
}

export async function renameSession(sessionId: string, name: string): Promise<Session> {
  const res = await authFetch(`${BASE}/api/v1/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await authFetch(`${BASE}/api/v1/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) throw new Error(await parseError(res));
}

export interface FeedbackPayload {
  description: string;
  screenshot?: string | null;
  task_id?: string | null;
  page_url?: string | null;
  user_agent?: string | null;
}

export async function submitFeedback(payload: FeedbackPayload): Promise<{ id: string; status: string; webhook_sent: boolean }> {
  const res = await authFetch(`${BASE}/api/v1/feedback`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
