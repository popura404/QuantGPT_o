import { X } from "lucide-react";
import type { Task } from "../../types/backtest";
import { TERMINAL_TASK_STATUSES as TERMINAL } from "../../types/backtest";
import { useColorMode } from "../../contexts/ColorModeContext";
import TaskStatusBadge from "./TaskStatusBadge";

function taskTypeLabel(taskType?: string): string {
  if (taskType === "strategy_backtest") return "策略回测";
  if (taskType === "composite") return "组合回测";
  if (taskType === "iteration") return "因子迭代";
  if (taskType === "wq_brain_submit") return "WQ 模拟";
  if (taskType === "wq_brain_batch") return "WQ 批量模拟";
  if (taskType === "wq_brain_batch_submit_by_id") return "WQ 批量提交";
  if (taskType === "wq_brain_finalize") return "WQ SC 确认";
  return taskType || "单因子回测";
}

interface Props {
  task: Task;
  onCancel?: () => void;
}

export default function TaskProgressPanel({ task, onCancel }: Props) {
  const { isDark } = useColorMode();
  const canCancel = Boolean(onCancel && task.task_id !== "error" && !task.cancel_requested && !TERMINAL.has(String(task.status)));
  const progress = typeof task.progress === "number" ? Math.max(0, Math.min(100, task.progress)) : null;
  const completed = task.completed ?? task.completed_combinations;
  const type = taskTypeLabel(task.task_type);

  return (
    <section className={`rounded-xl border p-4 ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-white"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <TaskStatusBadge status={task.status} />
            <span className={`text-sm font-medium ${isDark ? "text-gray-200" : "text-gray-800"}`}>{type}</span>
          </div>
          <div className={`mt-2 font-mono text-xs ${isDark ? "text-gray-500" : "text-gray-400"}`}>{task.task_id}</div>
        </div>
        {canCancel && (
          <button
            type="button"
            onClick={onCancel}
            className={`inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
              isDark
                ? "border-gray-700 text-gray-300 hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-300"
                : "border-gray-200 text-gray-600 hover:border-red-200 hover:bg-red-50 hover:text-red-700"
            }`}
          >
            <X className="h-3.5 w-3.5" />
            取消
          </button>
        )}
      </div>

      {task.error && <p role="alert" className="mt-3 text-sm text-red-700">{task.error}</p>}
      {task.status === "local_wait_cancelled" && <p className="mt-3 text-xs text-amber-700">已停止本地等待；远端任务是否停止仍需核对。</p>}
      {["remote_outcome_unknown", "reconciliation_required"].includes(task.status) && <p className="mt-3 text-xs text-amber-700">远端结果尚未确认，请对账后再决定下一步，避免重复提交。</p>}
      {task.status === "interrupted" && <p className="mt-3 text-xs text-amber-700">执行中断；当前结果不完整。{task.retryable ? "服务端标记为可重试。" : "请检查服务端任务状态。"}</p>}
      {task.next_action && <p className="mt-2 text-xs text-gray-500">下一步：{task.next_action}</p>}

      {(progress != null || task.progress_message || completed != null || task.expression) && (
        <div className="mt-4 space-y-3">
          {progress != null && (
            <div>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className={isDark ? "text-gray-400" : "text-gray-500"}>进度</span>
                <span className={`font-mono ${isDark ? "text-gray-300" : "text-gray-700"}`}>{progress.toFixed(0)}%</span>
              </div>
              <div className={`h-2 overflow-hidden rounded-full ${isDark ? "bg-gray-800" : "bg-gray-100"}`}>
                <div className="h-full rounded-full bg-blue-600 transition-all" style={{ width: `${progress}%` }} />
              </div>
            </div>
          )}
          {task.progress_message && (
            <div className={`rounded-md border px-3 py-2 text-xs ${isDark ? "border-gray-700 bg-gray-800 text-gray-300" : "border-gray-100 bg-gray-50 text-gray-600"}`}>
              {task.progress_message}
            </div>
          )}
          {completed != null && (
            <div className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>已完成: {completed}</div>
          )}
          {task.expression && (
            <code className={`block break-all rounded-md border px-3 py-2 text-xs ${isDark ? "border-gray-700 bg-gray-800 text-amber-300" : "border-gray-100 bg-gray-50 text-blue-700"}`}>
              {task.expression}
            </code>
          )}
        </div>
      )}
    </section>
  );
}
