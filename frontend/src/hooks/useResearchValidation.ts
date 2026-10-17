import { useCallback, useEffect, useRef, useState } from "react";
import { getTask, streamTask } from "../api/client";
import { getResearchValidation, submitResearchValidation, type ResearchValidationProfile } from "../api/research";
import { TERMINAL_TASK_STATUSES, type Task } from "../types/backtest";

export function useResearchValidation(projectId: string, evaluationId: string, selectionEvaluationId?: string) {
  const [task, setTask] = useState<Task | null>(null);
  const [profile, setProfile] = useState<ResearchValidationProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const closeRef = useRef<(() => void) | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const key = `quantgpt_validation_task_${projectId}_${evaluationId}`;

  const stop = useCallback(() => {
    generation.current++;
    closeRef.current?.();
    closeRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const observe = useCallback((initial: Task, current: () => boolean, signal: AbortSignal) => {
    let finished = false;
    const receive = (updated: Task) => {
      if (!current() || finished) return;
      setTask(updated);
      if (!TERMINAL_TASK_STATUSES.has(updated.status)) return;
      finished = true;
      closeRef.current?.();
      closeRef.current = null;
      if (updated.status !== "completed") {
        setError(updated.error ?? `离线验证任务未完成：${updated.status}`);
        setBusy(false);
        localStorage.removeItem(key);
        return;
      }
      // Completion is evidence of a finished computation; the profile still owns its blockers.
      void getResearchValidation(projectId, evaluationId, signal).then((value) => {
        if (!current()) return;
        setProfile(value);
        setError(null);
        localStorage.removeItem(key);
      }).catch((err) => {
        if (current()) setError(err instanceof Error ? err.message : "验证结果读取失败");
      }).finally(() => { if (current()) setBusy(false); });
    };
    receive(initial);
    if (finished || !current()) return;
    setBusy(true);
    closeRef.current = streamTask(initial.task_id, receive, () => {
      if (!current() || finished) return;
      void getTask(initial.task_id, signal).then(receive).catch((err) => {
        if (current()) { setError(err instanceof Error ? err.message : "验证任务读取失败"); setBusy(false); }
      });
    }, (message, fatal) => {
      if (!current()) return;
      setError(message);
      if (fatal) setBusy(false);
    });
  }, [projectId, evaluationId, key]);

  useEffect(() => {
    stop();
    const revision = generation.current;
    const current = () => generation.current === revision;
    const controller = new AbortController();
    abortRef.current = controller;
    setTask(null); setProfile(null); setError(null); setBusy(true);
    void (async () => {
      try {
        const value = await getResearchValidation(projectId, evaluationId, controller.signal);
        if (!current()) return;
        setProfile(value);
      } catch (err) {
        if (!current()) return;
        setError(err instanceof Error ? err.message : "验证结果读取失败");
      }
      const saved = localStorage.getItem(key);
      if (!saved) { if (current()) setBusy(false); return; }
      try {
        const restored = await getTask(saved, controller.signal);
        if (current()) observe(restored, current, controller.signal);
      } catch (err) {
        if (current()) { setError(err instanceof Error ? err.message : "验证任务恢复失败"); setBusy(false); }
      }
    })();
    return stop;
  }, [projectId, evaluationId, key, stop, observe]);

  const start = useCallback(async () => {
    stop();
    const revision = generation.current;
    const current = () => generation.current === revision;
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true); setError(null); setTask(null);
    const requestKey = `${key}_request_${selectionEvaluationId ?? "none"}`;
    const idempotencyKey = localStorage.getItem(requestKey) ?? crypto.randomUUID();
    localStorage.setItem(requestKey, idempotencyKey);
    try {
      const saved = localStorage.getItem(key);
      if (saved) {
        const restored = await getTask(saved, controller.signal);
        if (current()) observe(restored, current, controller.signal);
        return;
      }
      const accepted = await submitResearchValidation(projectId, evaluationId, selectionEvaluationId, idempotencyKey);
      // Keep accepted work recoverable if the user left this project while submitting.
      localStorage.setItem(key, accepted.task_id);
      localStorage.removeItem(requestKey);
      if (!current()) return;
      const initial = await getTask(accepted.task_id, controller.signal);
      if (current()) observe(initial, current, controller.signal);
    } catch (err) {
      if (current()) { setError(err instanceof Error ? err.message : "离线验证提交失败"); setBusy(false); }
    }
  }, [projectId, evaluationId, selectionEvaluationId, key, stop, observe]);

  return { task, profile, busy, error, start };
}
