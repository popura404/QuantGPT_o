import { useState } from "react";
import { ExternalLink, Send } from "lucide-react";
import type { Task, WQBrainTaskResult } from "../../types/backtest";
import ReportLink from "../ReportLink";
import TaskDetailDrawer from "../tasks/TaskDetailDrawer";
import TaskProgressPanel from "../tasks/TaskProgressPanel";
import WQBrainPreflightPanel from "./WQBrainPreflightPanel";

interface Props {
  task: Task | null;
  submitting: boolean;
  submitResult: WQBrainTaskResult | null;
  onFormalSubmit: (taskId: string, reason?: string | null) => void;
}

export default function WQBrainTaskPanel({ task, submitting, submitResult, onFormalSubmit }: Props) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [reason, setReason] = useState("");
  const result = task?.result as WQBrainTaskResult | undefined;
  const alphaId = result?.alpha_id;
  const preflight = submitResult?.submission_preflight ?? result?.submission_preflight;

  if (!task) {
    return (
      <section className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-500">
        暂无 WQ 任务
      </section>
    );
  }

  return (
    <section className="space-y-3 rounded-xl border border-gray-200 bg-white p-4">
      <TaskProgressPanel task={task} />
      {preflight && <WQBrainPreflightPanel preflight={preflight} />}
      {alphaId && (
        <div className="grid gap-2 md:grid-cols-[1fr_auto]">
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="正式提交 override reason（可选）"
            className="rounded-md border border-gray-200 px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={() => onFormalSubmit(task.task_id, reason || null)}
            disabled={submitting}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
            正式提交 alpha
          </button>
        </div>
      )}
      {submitResult && (
        <pre className="max-h-64 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-gray-700">
          {JSON.stringify(submitResult, null, 2)}
        </pre>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => setDrawerOpen(true)} className="inline-flex items-center gap-2 rounded-md border border-gray-300 px-3 py-2 text-sm">
          共享详情
        </button>
        {typeof result?.report_url === "string" && (
          <ReportLink reportUrl={result.report_url} className="inline-flex items-center gap-2 rounded-md border border-gray-300 px-3 py-2 text-sm">
            <ExternalLink className="h-4 w-4" />
            报告
          </ReportLink>
        )}
      </div>
      <TaskDetailDrawer task={task} open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </section>
  );
}
