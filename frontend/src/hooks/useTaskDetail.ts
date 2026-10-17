import { useEffect, useState } from "react";
import { getTask } from "../api/client";
import type { Task } from "../types/backtest";

interface DetailState {
  source: Task;
  task: Task | null;
  error: string | null;
}

/** Resolve list summaries only while selected, cancelling obsolete detail requests. */
export function useTaskDetail(source: Task | null) {
  const [resolved, setResolved] = useState<DetailState | null>(null);
  useEffect(() => {
    setResolved(null);
    if (!source?.summary_only) return;
    const controller = new AbortController();
    let current = true;
    void getTask(source.task_id, controller.signal).then((task) => {
      if (current) setResolved({ source, task, error: null });
    }).catch((err) => {
      if (current) setResolved({ source, task: null, error: err instanceof Error ? err.message : "任务详情读取失败" });
    });
    return () => { current = false; controller.abort(); };
  }, [source]);

  if (!source?.summary_only) return { task: source, loading: false, error: null };
  const selected = resolved?.source === source ? resolved : null;
  return { task: selected?.task ?? null, loading: !selected, error: selected?.error ?? null };
}
