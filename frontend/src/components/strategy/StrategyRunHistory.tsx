import { ExternalLink, RefreshCw } from "lucide-react";
import type { StrategyRunRecord } from "../../types/strategy";
import ReportLink from "../ReportLink";

interface Props {
  runs: StrategyRunRecord[];
  loading: boolean;
  onRefresh: () => void;
}

export default function StrategyRunHistory({ runs, loading, onRefresh }: Props) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Run History</h3>
        <button type="button" onClick={onRefresh} className="rounded-md border border-gray-300 p-1.5 text-gray-600">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>
      <div className="max-h-64 space-y-2 overflow-auto">
        {runs.length === 0 && <div className="py-6 text-center text-sm text-gray-400">暂无保存的 run</div>}
        {runs.map((run) => (
          <div key={run.id} className="rounded-md border border-gray-100 p-3">
            <div className="font-mono text-xs text-gray-500">{run.task_id ?? run.id}</div>
            <div className="mt-1 text-xs text-gray-400">{run.created_at ?? "-"}</div>
            {run.report_url && (
              <ReportLink reportUrl={run.report_url} className="mt-2 inline-flex items-center gap-1 text-xs text-blue-700">
                <ExternalLink className="h-3 w-3" />
                报告
              </ReportLink>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
