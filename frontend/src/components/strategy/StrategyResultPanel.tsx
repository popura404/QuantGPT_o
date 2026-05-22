import { Download, ExternalLink, Save } from "lucide-react";
import type { StrategyBacktestTaskResult, StrategyExportPayload } from "../../types/strategy";
import ReportLink from "../ReportLink";

function formatValue(value: unknown): string {
  if (typeof value === "number") return Number.isFinite(value) ? value.toFixed(4) : "-";
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

interface Props {
  result: StrategyBacktestTaskResult | null;
  exportPayload: StrategyExportPayload | null;
  exporting: boolean;
  savingRun: boolean;
  onExport: () => void;
  onSaveRun: () => void;
}

export default function StrategyResultPanel({ result, exportPayload, exporting, savingRun, onExport, onSaveRun }: Props) {
  const strategy = result?.strategy_result;
  const score = result?.strategy_score ?? strategy?.oos_score;
  if (!strategy && !score) return null;
  const holdings = strategy?.latest_holdings ?? [];
  const validationIssues = strategy?.validation_issues ?? score?.validation_issues ?? [];
  const riskLogs = strategy?.risk_logs ?? score?.risk_logs ?? [];

  return (
    <section className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900">策略结果</h3>
        <div className="flex gap-2">
          {result?.report_url && (
            <ReportLink reportUrl={result.report_url} className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-xs">
              <ExternalLink className="h-3.5 w-3.5" />
              报告
            </ReportLink>
          )}
          <button type="button" onClick={onSaveRun} disabled={savingRun || !strategy} className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-xs disabled:opacity-50">
            <Save className="h-3.5 w-3.5" />
            保存 run
          </button>
          <button type="button" onClick={onExport} disabled={exporting || !strategy} className="inline-flex items-center gap-1.5 rounded-md bg-gray-900 px-3 py-1.5 text-xs text-white disabled:opacity-50">
            <Download className="h-3.5 w-3.5" />
            导出
          </button>
        </div>
      </div>
      {score && (
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {[
            ["score", score.score],
            ["grade", score.grade],
            ["decision", score.decision],
            ["risk", score.overfit_risk],
          ].map(([label, value]) => (
            <Metric key={String(label)} label={String(label)} value={value} />
          ))}
        </div>
      )}
      {strategy && (
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {[
            ["experiment", strategy.experiment_id],
            ["factor_hash", strategy.factor_hash],
            ["snapshot", strategy.data_snapshot_id],
            ["promotion", strategy.promotion_state],
          ].map(([label, value]) => (
            <Metric key={String(label)} label={String(label)} value={value} />
          ))}
        </div>
      )}
      {strategy?.promotion_blockers && strategy.promotion_blockers.length > 0 && (
        <JsonBlock title="Promotion blockers" value={strategy.promotion_blockers} />
      )}
      {strategy?.oos_result && <JsonBlock title="OOS summary" value={strategy.oos_result} />}
      {strategy?.data_quality && <JsonBlock title="Data quality" value={strategy.data_quality} />}
      {holdings.length > 0 && <JsonBlock title="Latest holdings" value={holdings.slice(0, 12)} />}
      {validationIssues.length > 0 && <JsonBlock title="Validation issues" value={validationIssues} />}
      {riskLogs.length > 0 && <JsonBlock title="Risk logs" value={riskLogs} />}
      {exportPayload && (
        <JsonBlock
          title="Candidate export"
          value={{
            schema_version: exportPayload.schema_version,
            experiment_id: exportPayload.experiment_id,
            factor_hash: exportPayload.factor_hash,
            notice: exportPayload.notice,
            validation_summary: exportPayload.validation_summary,
            signals: exportPayload.signals.slice(0, 12),
          }}
        />
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-md bg-gray-50 p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-1 font-mono text-sm text-gray-900">{formatValue(value)}</div>
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <div className="mb-2 text-xs font-medium uppercase text-gray-500">{title}</div>
      <pre className="max-h-72 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-gray-700">{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}
