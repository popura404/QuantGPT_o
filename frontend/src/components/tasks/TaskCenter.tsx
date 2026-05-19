import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { fetchTasks } from "../../api/client";
import { useColorMode } from "../../contexts/ColorModeContext";
import type { Task } from "../../types/backtest";
import TaskDetailDrawer from "./TaskDetailDrawer";
import TaskStatusBadge from "./TaskStatusBadge";

const TYPE_OPTIONS = [
  ["", "全部类型"],
  ["backtest", "单因子"],
  ["composite", "组合"],
  ["strategy_backtest", "策略"],
  ["wq_brain_submit", "WQ 模拟"],
  ["wq_brain_batch", "WQ 批量"],
  ["wq_brain_batch_submit_by_id", "WQ 提交"],
];

const STATUS_OPTIONS = [
  ["", "全部状态"],
  ["running", "运行中"],
  ["completed", "已完成"],
  ["failed", "失败"],
  ["cancelled", "已取消"],
];

interface Props {
  sessionId?: string | null;
}

export default function TaskCenter({ sessionId }: Props) {
  const { isDark } = useColorMode();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskType, setTaskType] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Task | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchTasks(1, 50, sessionId ?? undefined, taskType || undefined, status || undefined);
      setTasks(data.tasks);
    } catch (err) {
      setError(err instanceof Error ? err.message : "任务读取失败");
    } finally {
      setLoading(false);
    }
  }, [sessionId, taskType, status]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className={`rounded-xl border ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-white"}`}>
      <div className={`flex flex-wrap items-center justify-between gap-3 border-b p-4 ${isDark ? "border-gray-800" : "border-gray-100"}`}>
        <div>
          <h3 className={`text-sm font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>任务中心</h3>
          <p className={`mt-1 text-xs ${isDark ? "text-gray-500" : "text-gray-400"}`}>统一查看回测、组合、策略和 WQ 任务</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={taskType} onChange={(event) => setTaskType(event.target.value)} className="h-8 rounded-md border border-gray-300 bg-white px-2 text-xs">
            {TYPE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select value={status} onChange={(event) => setStatus(event.target.value)} className="h-8 rounded-md border border-gray-300 bg-white px-2 text-xs">
            {STATUS_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <button type="button" onClick={() => void load()} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-300 px-2 text-xs">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            刷新
          </button>
        </div>
      </div>
      {error && <div className="m-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      <div className="divide-y divide-gray-100">
        {tasks.length === 0 && !loading && <div className={`p-8 text-center text-sm ${isDark ? "text-gray-500" : "text-gray-400"}`}>暂无任务</div>}
        {tasks.map((task) => (
          <button
            type="button"
            key={task.task_id}
            onClick={() => setSelected(task)}
            className={`flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors ${isDark ? "hover:bg-gray-800" : "hover:bg-gray-50"}`}
          >
            <div className="min-w-0">
              <div className={`truncate font-mono text-xs ${isDark ? "text-gray-300" : "text-gray-700"}`}>{task.task_id}</div>
              <div className={`mt-1 truncate text-xs ${isDark ? "text-gray-500" : "text-gray-400"}`}>{task.task_type || "backtest"}</div>
            </div>
            <TaskStatusBadge status={task.status} compact />
          </button>
        ))}
      </div>
      <TaskDetailDrawer task={selected} open={Boolean(selected)} onClose={() => setSelected(null)} />
    </section>
  );
}
