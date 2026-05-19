import { AlertCircle, CheckCircle2, Clock, Loader2, Send, XCircle } from "lucide-react";
import type { TaskStatus } from "../../types/backtest";
import { useColorMode } from "../../contexts/ColorModeContext";

const LABELS: Record<string, string> = {
  pending: "等待中",
  queued: "排队中",
  running: "运行中",
  authenticating: "认证中",
  generating_expression: "生成表达式",
  validating: "校验中",
  fetching_data: "拉取数据",
  checking_data_quality: "数据质量",
  fetching_fundamentals: "财务数据",
  backtesting: "回测中",
  simulating: "模拟中",
  submitted: "已提交",
  finalizing: "确认中",
  analyzing: "分析中",
  generating_report: "生成报告",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  iterating: "迭代中",
  iteration_completed: "迭代完成",
};

function statusTone(status: string) {
  if (status === "completed" || status === "iteration_completed") return "success";
  if (status === "failed") return "danger";
  if (status === "cancelled") return "muted";
  if (status === "submitted" || status === "finalizing") return "purple";
  if (status === "pending" || status === "queued") return "neutral";
  if (LABELS[status]) return "active";
  return "neutral";
}

export function taskStatusLabel(status: TaskStatus | string | undefined): string {
  if (!status) return "未知";
  return LABELS[String(status)] ?? String(status);
}

interface Props {
  status?: TaskStatus | string;
  compact?: boolean;
}

export default function TaskStatusBadge({ status, compact = false }: Props) {
  const { isDark } = useColorMode();
  const raw = String(status ?? "unknown");
  const tone = statusTone(raw);
  const className = {
    success: isDark ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30" : "bg-emerald-50 text-emerald-700 border-emerald-200",
    danger: isDark ? "bg-red-500/10 text-red-300 border-red-500/30" : "bg-red-50 text-red-700 border-red-200",
    muted: isDark ? "bg-gray-800 text-gray-300 border-gray-700" : "bg-gray-50 text-gray-600 border-gray-200",
    purple: isDark ? "bg-violet-500/10 text-violet-300 border-violet-500/30" : "bg-violet-50 text-violet-700 border-violet-200",
    active: isDark ? "bg-blue-500/10 text-blue-300 border-blue-500/30" : "bg-blue-50 text-blue-700 border-blue-200",
    neutral: isDark ? "bg-slate-800 text-slate-300 border-slate-700" : "bg-slate-50 text-slate-600 border-slate-200",
  }[tone];
  const Icon = tone === "success"
    ? CheckCircle2
    : tone === "danger"
      ? XCircle
      : tone === "purple"
        ? Send
        : tone === "active"
          ? Loader2
          : raw === "pending" || raw === "queued"
            ? Clock
            : AlertCircle;
  const spinning = tone === "active";

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border font-medium ${compact ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm"} ${className}`}>
      <Icon className={`${compact ? "h-3 w-3" : "h-3.5 w-3.5"} ${spinning ? "animate-spin" : ""}`} />
      {taskStatusLabel(raw)}
    </span>
  );
}
