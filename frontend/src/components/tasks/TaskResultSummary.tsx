import { ExternalLink } from "lucide-react";
import { getReportUrl } from "../../api/client";
import { useColorMode } from "../../contexts/ColorModeContext";
import type { BacktestResult, Task } from "../../types/backtest";

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function formatValue(value: unknown): string {
  if (typeof value === "number") return Number.isFinite(value) ? value.toFixed(4) : "-";
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function metricGrid(metrics: Record<string, unknown> | undefined, keys: string[]) {
  if (!metrics) return [];
  return keys
    .map((key) => ({ label: key, value: metrics[key] }))
    .filter((item) => item.value !== undefined && item.value !== null);
}

function isBacktestResult(result: Task["result"] | undefined): result is BacktestResult {
  return Boolean(result && "params" in result && "metrics" in result && "backtest_summary" in result);
}

interface Props {
  task: Task;
}

export default function TaskResultSummary({ task }: Props) {
  const { isDark } = useColorMode();
  const result = task.result;
  const box = `rounded-lg border p-3 ${isDark ? "border-gray-700 bg-gray-800" : "border-gray-100 bg-gray-50"}`;
  const muted = isDark ? "text-gray-400" : "text-gray-500";
  const primary = isDark ? "text-gray-100" : "text-gray-900";

  if (task.status === "failed") {
    return (
      <div className={`rounded-lg border p-3 text-sm ${isDark ? "border-red-500/30 bg-red-500/10 text-red-300" : "border-red-200 bg-red-50 text-red-700"}`}>
        {typeof task.error === "string" ? task.error : JSON.stringify(task.error ?? "未知错误")}
      </div>
    );
  }

  if (!result) {
    return <div className={`text-sm ${muted}`}>暂无结果</div>;
  }

  if (task.task_type === "strategy_backtest" && isObject(result)) {
    const strategy = isObject(result.strategy_result) ? result.strategy_result : {};
    const score = isObject(result.strategy_score) ? result.strategy_score : isObject(strategy.oos_score) ? strategy.oos_score : {};
    const holdings = Array.isArray(strategy.latest_holdings) ? strategy.latest_holdings : [];
    return (
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          {([
            ["score", score.score],
            ["grade", score.grade],
            ["decision", score.decision],
            ["risk", score.overfit_risk],
          ] as Array<[string, unknown]>).map(([label, value]) => (
            <div key={label} className={box}>
              <div className={`text-xs ${muted}`}>{label}</div>
              <div className={`mt-1 font-mono text-sm ${primary}`}>{formatValue(value)}</div>
            </div>
          ))}
        </div>
        {isObject(strategy.oos_result) && <JsonBlock title="OOS" value={strategy.oos_result} />}
        {isObject(strategy.data_quality) && <JsonBlock title="Data Quality" value={strategy.data_quality} />}
        {holdings.length > 0 && <JsonBlock title="Latest Holdings" value={holdings.slice(0, 8)} />}
        {typeof result.report_url === "string" && <ReportLink href={result.report_url} />}
      </div>
    );
  }

  if (String(task.task_type ?? "").startsWith("wq_brain") || isObject(result) && ("alpha_id" in result || "is_metrics" in result)) {
    const res = result as Record<string, unknown>;
    const isMetrics = isObject(res.is_metrics) ? res.is_metrics : {};
    const oosMetrics = isObject(res.oos_metrics) ? res.oos_metrics : {};
    return (
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          {([
            ["alpha_id", res.alpha_id],
            ["submitted", res.submitted],
            ["submit_state", res.submit_state ?? res.final_status ?? res.status],
            ["platform", res.platform_status],
          ] as Array<[string, unknown]>).map(([label, value]) => (
            <div key={label} className={box}>
              <div className={`text-xs ${muted}`}>{label}</div>
              <div className={`mt-1 font-mono text-sm ${primary}`}>{formatValue(value)}</div>
            </div>
          ))}
        </div>
        {metricGrid(isMetrics, ["sharpe", "fitness", "turnover", "returns", "margin"]).length > 0 && <MetricList title="IS Metrics" metrics={isMetrics} />}
        {Object.keys(oosMetrics).length > 0 && <MetricList title="OOS Metrics" metrics={oosMetrics} />}
        {isObject(res.submission_preflight) && <JsonBlock title="Submission Preflight" value={res.submission_preflight} />}
        {isObject(res.summary) && <JsonBlock title="Summary" value={res.summary} />}
        {isObject(res.sub_results) && <JsonBlock title="Sub Results" value={res.sub_results} />}
      </div>
    );
  }

  if (isBacktestResult(result)) {
    const summary = result.backtest_summary as unknown as Record<string, unknown>;
    const title = task.task_type === "composite" ? "组合结果" : "回测结果";
    return (
      <div className="space-y-3">
        <div>
          <div className={`text-sm font-medium ${primary}`}>{title}</div>
          <code className={`mt-2 block break-all rounded-md border px-3 py-2 text-xs ${isDark ? "border-gray-700 bg-gray-800 text-amber-300" : "border-gray-100 bg-gray-50 text-blue-700"}`}>
            {result.params.expression}
          </code>
        </div>
        <MetricList
          title="Core Metrics"
          metrics={{
            sharpe: result.metrics.sharpe,
            cagr: result.metrics.cagr,
            max_drawdown: result.metrics.max_drawdown,
            long_short_sharpe: summary.long_short_sharpe,
            rank_ic_mean: summary.rank_ic_mean,
            ic_ir: summary.ic_ir,
            turnover: summary.turnover,
            wq_fitness: summary.wq_fitness,
          }}
        />
        {result.oos_result && <JsonBlock title="OOS / Data Quality" value={{ oos_result: result.oos_result, data_quality: result.data_quality }} />}
        {result.report_url && <ReportLink href={result.report_url} />}
      </div>
    );
  }

  return <JsonBlock title="Result" value={result} />;
}

function MetricList({ title, metrics }: { title: string; metrics: Record<string, unknown> }) {
  const { isDark } = useColorMode();
  const entries = Object.entries(metrics).filter(([, value]) => value !== undefined && value !== null);
  if (entries.length === 0) return null;
  return (
    <div>
      <div className={`mb-2 text-xs font-medium uppercase ${isDark ? "text-gray-400" : "text-gray-500"}`}>{title}</div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {entries.map(([key, value]) => (
          <div key={key} className={`rounded-lg border p-3 ${isDark ? "border-gray-700 bg-gray-800" : "border-gray-100 bg-gray-50"}`}>
            <div className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>{key}</div>
            <div className={`mt-1 font-mono text-sm ${isDark ? "text-gray-100" : "text-gray-900"}`}>{formatValue(value)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  const { isDark } = useColorMode();
  return (
    <div>
      <div className={`mb-2 text-xs font-medium uppercase ${isDark ? "text-gray-400" : "text-gray-500"}`}>{title}</div>
      <pre className={`max-h-72 overflow-auto rounded-lg border p-3 text-xs ${isDark ? "border-gray-700 bg-gray-900 text-gray-300" : "border-gray-100 bg-white text-gray-700"}`}>
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function ReportLink({ href }: { href: string }) {
  return (
    <a
      href={getReportUrl(href)}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
    >
      <ExternalLink className="h-4 w-4" />
      打开报告
    </a>
  );
}
