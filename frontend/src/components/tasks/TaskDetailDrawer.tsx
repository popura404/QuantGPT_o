import { X } from "lucide-react";
import type { Task } from "../../types/backtest";
import { useColorMode } from "../../contexts/ColorModeContext";
import TaskProgressPanel from "./TaskProgressPanel";
import TaskResultSummary from "./TaskResultSummary";

interface Props {
  task: Task | null;
  open: boolean;
  onClose: () => void;
  onCancel?: (task: Task) => void;
}

export default function TaskDetailDrawer({ task, open, onClose, onCancel }: Props) {
  const { isDark } = useColorMode();
  if (!open || !task) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <aside
        className={`h-full w-full max-w-2xl overflow-y-auto border-l p-5 shadow-2xl ${
          isDark ? "border-gray-800 bg-gray-950" : "border-gray-200 bg-white"
        }`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h3 className={`text-lg font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>任务详情</h3>
            <div className={`mt-1 font-mono text-xs ${isDark ? "text-gray-500" : "text-gray-400"}`}>{task.task_id}</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className={`rounded-md p-2 transition-colors ${isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-500 hover:bg-gray-100"}`}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-4">
          <TaskProgressPanel task={task} onCancel={onCancel ? () => onCancel(task) : undefined} />
          <TaskResultSummary task={task} />
        </div>
      </aside>
    </div>
  );
}
