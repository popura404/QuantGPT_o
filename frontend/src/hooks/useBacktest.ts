import { useState, useRef, useCallback } from "react";
import type { BacktestResult, Task, BacktestRequest } from "../types/backtest";
import { TERMINAL_TASK_STATUSES } from "../types/backtest";
import { submitBacktest, streamTask, submitIteration, selectCandidate, cancelTask, getTask } from "../api/client";

function isBacktestResult(result: Task["result"] | undefined): result is BacktestResult {
  return Boolean(result && "params" in result && "metrics" in result && "backtest_summary" in result);
}

export function useBacktest(onComplete?: (task: Task) => void, sessionId?: string | null) {
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  // Map from parentTaskId → iterationTask, so each task has its own iteration state
  const [iterationMap, setIterationMap] = useState<Record<string, Task>>({});
  const [isIterating, setIsIterating] = useState(false);
  const closeRef = useRef<(() => void) | null>(null);

  // Derived: iteration task for the currently active task
  const iterationTask = activeTask ? (iterationMap[activeTask.task_id] ?? null) : null;

  const stopStream = useCallback(() => {
    closeRef.current?.();
    closeRef.current = null;
  }, []);

  const submit = useCallback(
    async (req: BacktestRequest) => {
      stopStream();
      setIsLoading(true);
      setIsIterating(false);
      try {
        const { task_id } = await submitBacktest(req, sessionId ?? undefined);
        const initial: Task = { task_id, status: "pending", params: req };
        setActiveTask(initial);

        closeRef.current = streamTask(
          task_id,
          (task) => {
            setActiveTask(task);
            if (TERMINAL_TASK_STATUSES.has(task.status)) {
              setIsLoading(false);
              onComplete?.(task);
            }
          },
          () => { setIsLoading(false); },
          () => { setIsLoading(false); },
        );
      } catch (err) {
        setIsLoading(false);
        setActiveTask({
          task_id: "error",
          status: "failed",
          error: err instanceof Error ? err.message : "Unknown error",
        });
      }
    },
    [stopStream, onComplete, sessionId]
  );

  const iterate = useCallback(
    async (taskId: string, nCandidates = 5, direction?: string) => {
      stopStream();
      setIsIterating(true);
      setIterationMap((prev) => ({ ...prev, [taskId]: {
        task_id: "",
        status: "pending",
        task_type: "iteration",
        parent_task_id: taskId,
        candidates: [],
        candidates_done: 0,
        candidates_total: nCandidates,
      }}));
      try {
        const { task_id } = await submitIteration(taskId, nCandidates, direction);
        const initial: Task = {
          task_id,
          status: "pending",
          task_type: "iteration",
          parent_task_id: taskId,
          candidates: [],
          candidates_done: 0,
          candidates_total: nCandidates,
        };
        setIterationMap((prev) => ({ ...prev, [taskId]: initial }));

        closeRef.current = streamTask(
          task_id,
          (task) => {
            setIterationMap((prev) => ({ ...prev, [taskId]: task }));
            if (TERMINAL_TASK_STATUSES.has(task.status)) {
              setIsIterating(false);
            }
          },
          () => { setIsIterating(false); },
          () => { setIsIterating(false); },
        );
      } catch (err) {
        setIsIterating(false);
        setIterationMap((prev) => ({ ...prev, [taskId]: {
          task_id: "error",
          status: "failed",
          error: err instanceof Error ? err.message : "Unknown error",
        }}));
      }
    },
    [stopStream]
  );

  const handleSelectCandidate = useCallback(
    async (iterTaskId: string, index: number) => {
      try {
        const result = await selectCandidate(iterTaskId, index);
        if (result.expression) {
          setActiveTask((prev) => {
            if (!prev || !isBacktestResult(prev.result)) return prev;
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
        // Mark selected in the iteration task for this parent
        setIterationMap((prev) => {
          const entry = Object.entries(prev).find(([, t]) => t.task_id === iterTaskId);
          if (!entry) return prev;
          const [parentId, task] = entry;
          return { ...prev, [parentId]: { ...task, selected_candidate_index: index } };
        });
      } catch (err) {
        console.error("Select candidate failed:", err);
      }
    },
    []
  );

  const cancel = useCallback(async () => {
    if (!activeTask || !activeTask.task_id || activeTask.task_id === "error") return;
    try {
      await cancelTask(activeTask.task_id);
      const confirmed = await getTask(activeTask.task_id);
      setActiveTask(confirmed);
      if (TERMINAL_TASK_STATUSES.has(confirmed.status)) { stopStream(); setIsLoading(false); }
    } catch (err) {
      setActiveTask((prev) => prev ? { ...prev, error: err instanceof Error ? err.message : "取消请求失败，任务状态尚未确认" } : prev);
    }
  }, [activeTask, stopStream]);

  return {
    activeTask,
    isLoading,
    submit,
    cancel,
    setActiveTask,
    iterationTask,
    isIterating,
    iterate,
    handleSelectCandidate,
  };
}
