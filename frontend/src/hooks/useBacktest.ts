import { useState, useRef, useCallback, useEffect } from "react";
import type { BacktestResult, Task, BacktestRequest } from "../types/backtest";
import { TERMINAL_TASK_STATUSES } from "../types/backtest";
import { submitBacktest, streamTask, submitIteration, selectCandidate, cancelTask, getTask } from "../api/client";
import { submitCompositeBacktest, type CompositeBacktestPayload } from "../api/composite";

function isBacktestResult(result: Task["result"] | undefined): result is BacktestResult {
  return Boolean(result && "params" in result && "metrics" in result && "backtest_summary" in result);
}

export function useBacktest(onComplete?: (task: Task) => void, sessionId?: string | null) {
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [iterationMap, setIterationMap] = useState<Record<string, Task>>({});
  const [isIterating, setIsIterating] = useState(false);
  const closeRef = useRef<(() => void) | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;
  const iterationTask = activeTask ? (iterationMap[activeTask.task_id] ?? null) : null;

  const stopStream = useCallback(() => {
    generation.current++;
    closeRef.current?.();
    closeRef.current = null;
    detailController.current?.abort();
    detailController.current = null;
  }, []);

  useEffect(() => {
    stopStream();
    setActiveTask(null);
    setIterationMap({});
    setIsLoading(false);
    setIsIterating(false);
    return stopStream;
  }, [sessionId, stopStream]);

  // Capture both the operation and its session before any asynchronous work.
  const captureCurrent = useCallback(() => {
    const current = generation.current;
    const currentSession = sessionId;
    return () => generation.current === current && sessionRef.current === currentSession;
  }, [sessionId]);

  const trackTask = useCallback((initial: Task, current: () => boolean) => {
    setActiveTask(initial);
    setIsLoading(!TERMINAL_TASK_STATUSES.has(initial.status));
    if (TERMINAL_TASK_STATUSES.has(initial.status)) return;
    closeRef.current = streamTask(
      initial.task_id,
      (task) => {
        if (!current()) return;
        setActiveTask(task);
        if (TERMINAL_TASK_STATUSES.has(task.status)) {
          setIsLoading(false);
          onComplete?.(task);
        }
      },
      () => { if (current()) setIsLoading(false); },
      (message, fatal) => {
        if (!current()) return;
        setActiveTask((task) => task ? { ...task, error: message } : task);
        if (fatal) setIsLoading(false);
      },
    );
  }, [onComplete]);

  const submitTask = useCallback(async (
    create: () => Promise<{ task_id: string }>,
    initial: Omit<Task, "task_id" | "status">,
    showFailure: boolean,
  ): Promise<boolean> => {
    stopStream();
    const current = captureCurrent();
    setIsLoading(true);
    setIsIterating(false);
    try {
      const { task_id } = await create();
      if (!current()) return false;
      trackTask({ ...initial, task_id, status: "pending" }, current);
      return true;
    } catch (err) {
      if (!current()) return false;
      setIsLoading(false);
      if (!showFailure) throw err;
      setActiveTask({ task_id: "error", status: "failed", error: err instanceof Error ? err.message : "提交失败" });
      return false;
    }
  }, [stopStream, captureCurrent, trackTask]);

  const submit = useCallback((req: BacktestRequest) => submitTask(
    () => submitBacktest(req, sessionId ?? undefined), { params: req, task_type: "backtest" }, true,
  ), [submitTask, sessionId]);

  // Let the still-mounted combination form display submission errors and keep its inputs.
  const submitComposite = useCallback((payload: CompositeBacktestPayload) => submitTask(
    () => submitCompositeBacktest({ ...payload, session_id: sessionId ?? undefined }), { task_type: "composite" }, false,
  ), [submitTask, sessionId]);

  const selectTask = useCallback((task: Task | null) => {
    stopStream();
    setIsLoading(false);
    setIsIterating(false);
    const current = captureCurrent();
    if (!task) { setActiveTask(null); return; }
    if (!task.summary_only) { trackTask(task, current); return; }
    const controller = new AbortController();
    detailController.current = controller;
    setActiveTask(task);
    setIsLoading(true);
    void getTask(task.task_id, controller.signal).then((detail) => {
      if (current()) trackTask(detail, current);
    }).catch((err) => {
      if (!current()) return;
      setIsLoading(false);
      setActiveTask({ ...task, error: err instanceof Error ? err.message : "任务详情读取失败" });
    });
  }, [stopStream, trackTask, captureCurrent]);

  const iterate = useCallback(async (taskId: string, nCandidates = 5, direction?: string) => {
    stopStream();
    const current = captureCurrent();
    setIsLoading(false);
    setIsIterating(true);
    const initial: Task = {
      task_id: "", status: "pending", task_type: "iteration", parent_task_id: taskId,
      candidates: [], candidates_done: 0, candidates_total: nCandidates,
    };
    setIterationMap((prev) => ({ ...prev, [taskId]: initial }));
    try {
      const { task_id } = await submitIteration(taskId, nCandidates, direction);
      if (!current()) return;
      setIterationMap((prev) => ({ ...prev, [taskId]: { ...initial, task_id } }));
      closeRef.current = streamTask(
        task_id,
        (task) => {
          if (!current()) return;
          setIterationMap((prev) => ({ ...prev, [taskId]: task }));
          if (TERMINAL_TASK_STATUSES.has(task.status)) setIsIterating(false);
        },
        () => { if (current()) setIsIterating(false); },
        (message, fatal) => {
          if (!current()) return;
          setIterationMap((prev) => ({ ...prev, [taskId]: { ...prev[taskId], error: message } }));
          if (fatal) setIsIterating(false);
        },
      );
    } catch (err) {
      if (!current()) return;
      setIsIterating(false);
      setIterationMap((prev) => ({ ...prev, [taskId]: {
        ...initial, task_id: "error", status: "failed", error: err instanceof Error ? err.message : "迭代提交失败",
      } }));
    }
  }, [stopStream, captureCurrent]);

  const handleSelectCandidate = useCallback(async (iterTaskId: string, index: number) => {
    const current = captureCurrent();
    const parentId = activeTask?.task_id;
    try {
      const result = await selectCandidate(iterTaskId, index);
      if (!current()) return;
      if (result.expression) {
        setActiveTask((prev) => {
          if (!prev || prev.task_id !== parentId || !isBacktestResult(prev.result)) return prev;
          return {
            ...prev,
            expression: result.expression as string,
            result: {
              ...prev.result,
              report_url: result.report_url as string,
              metrics: result.report_metrics as typeof prev.result.metrics,
              backtest_summary: result.backtest_summary as typeof prev.result.backtest_summary,
              params: { ...prev.result.params, expression: result.expression as string },
            },
          };
        });
      }
      setIterationMap((prev) => {
        const entry = Object.entries(prev).find(([, t]) => t.task_id === iterTaskId);
        if (!entry) return prev;
        const [parentId, task] = entry;
        return { ...prev, [parentId]: { ...task, selected_candidate_index: index } };
      });
    } catch (err) {
      if (!current()) return;
      setActiveTask((task) => task ? { ...task, error: err instanceof Error ? err.message : "候选选择失败" } : task);
    }
  }, [captureCurrent, activeTask?.task_id]);

  const cancel = useCallback(async () => {
    if (!activeTask || !activeTask.task_id || activeTask.task_id === "error") return;
    const current = captureCurrent();
    try {
      await cancelTask(activeTask.task_id);
      if (!current()) return;
      const confirmed = await getTask(activeTask.task_id);
      if (!current()) return;
      setActiveTask(confirmed);
      if (TERMINAL_TASK_STATUSES.has(confirmed.status)) { stopStream(); setIsLoading(false); }
    } catch (err) {
      if (!current()) return;
      setActiveTask((prev) => prev ? { ...prev, error: err instanceof Error ? err.message : "取消请求失败，任务状态尚未确认" } : prev);
    }
  }, [activeTask, stopStream, captureCurrent]);

  return { activeTask, isLoading, submit, submitComposite, cancel, selectTask, iterationTask, isIterating, iterate, handleSelectCandidate };
}
