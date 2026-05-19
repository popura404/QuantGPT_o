import type { Task } from "../types/backtest";
import { useColorMode } from "../contexts/ColorModeContext";
import TaskStatusBadge from "./tasks/TaskStatusBadge";

interface Props {
  task: Task;
  isActive: boolean;
  onClick: () => void;
}

export default function TaskHistoryItem({ task, isActive, onClick }: Props) {
  const { isDark } = useColorMode();
  const prompt = (task.params as any)?.prompt ?? task.result?.llm?.prompt ?? (task.result?.params as any)?.prompt ?? "—";
  const expression = task.expression ?? task.result?.params?.expression;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-xl border p-3 transition-colors ${
        isActive
          ? isDark ? "border-amber-500/50 bg-amber-500/10" : "border-blue-300 bg-blue-50/50"
          : isDark ? "border-gray-700 bg-gray-900 hover:border-gray-600" : "border-gray-200 bg-white hover:border-gray-300"
      }`}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center justify-between gap-2">
            <p className={`truncate text-sm ${isDark ? "text-gray-200" : "text-gray-800"}`}>{prompt}</p>
            <TaskStatusBadge status={task.status} compact />
          </div>
          {expression && (
            <p className="text-xs text-gray-400 font-mono truncate mt-0.5">{expression}</p>
          )}
        </div>
      </div>
    </button>
  );
}
