import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  ExternalLink,
  FlaskConical,
  Loader2,
  Play,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { authFetch, getReportUrl, parseError, streamTask } from "../api/client";
import {
  exportStrategyCandidate,
  instantiateStrategyTemplate,
  listStrategyTemplates,
  submitStrategyBacktest,
  validateStrategySpec,
} from "../api/strategy";
import { useAuth } from "../contexts/AuthContext";
import type { Task } from "../types/backtest";
import type {
  StrategyBacktestTaskResult,
  StrategyExportPayload,
  StrategyHolding,
  StrategyTemplateSummary,
  StrategyValidationResult,
} from "../types/strategy";

const DEFAULT_DATES = {
  start_date: "2024-01-02",
  end_date: "2024-03-29",
  benchmark: "hs300",
};

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled", "iteration_completed"]);

function isStrategyTaskResult(result: Task["result"]): result is StrategyBacktestTaskResult {
  return Boolean(result && typeof result === "object" && "strategy_result" in result);
}

function formatValue(value: unknown): string {
  if (typeof value === "number") return Number.isFinite(value) ? value.toFixed(4) : "-";
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function holdingWeight(holding: StrategyHolding): string {
  const value = holding.target_weight ?? holding.weight;
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "-";
}

export default function StrategyWorkbench() {
  const { isGuest } = useAuth();
  const [templates, setTemplates] = useState<StrategyTemplateSummary[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState("momentum_top_n_v1");
  const [specText, setSpecText] = useState("");
  const [validation, setValidation] = useState<StrategyValidationResult | null>(null);
  const [strategyTask, setStrategyTask] = useState<Task | null>(null);
  const [exportPayload, setExportPayload] = useState<StrategyExportPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [checkingReport, setCheckingReport] = useState(false);
  const closeStreamRef = useRef<(() => void) | null>(null);

  const parsedSpec = useMemo(() => {
    try {
      return JSON.parse(specText || "{}") as Record<string, unknown>;
    } catch {
      return null;
    }
  }, [specText]);

  const taskResult = isStrategyTaskResult(strategyTask?.result) ? strategyTask.result : null;
  const metrics = taskResult?.strategy_result?.metrics ?? {};
  const metricEntries = Object.entries(metrics).slice(0, 8);
  const latestHoldings = taskResult?.strategy_result?.latest_holdings ?? [];
  const selected = templates.find((template) => template.id === selectedTemplate);
  const submitDisabled = busy || isGuest;

  function stopStream() {
    closeStreamRef.current?.();
    closeStreamRef.current = null;
  }

  useEffect(() => {
    return () => stopStream();
  }, []);

  useEffect(() => {
    listStrategyTemplates()
      .then((items) => {
        setTemplates(items);
        if (items[0]) setSelectedTemplate(items[0].id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "模板加载失败"));
  }, []);

  useEffect(() => {
    if (!selectedTemplate) return;
    stopStream();
    setBusy(true);
    setError(null);
    setExportPayload(null);
    instantiateStrategyTemplate(selectedTemplate)
      .then((spec) => {
        setSpecText(JSON.stringify(spec, null, 2));
        setValidation(null);
        setStrategyTask(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "模板生成失败"))
      .finally(() => setBusy(false));
  }, [selectedTemplate]);

  async function handleValidate() {
    if (!parsedSpec) {
      setValidation({ is_valid: false, issues: [{ code: "JSON_INVALID", message: "JSON 格式错误" }] });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setValidation(await validateStrategySpec(parsedSpec));
    } catch (err) {
      setError(err instanceof Error ? err.message : "校验失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmit() {
    if (isGuest) {
      setError("请登录后提交策略回测");
      return;
    }
    if (!parsedSpec) {
      setValidation({ is_valid: false, issues: [{ code: "JSON_INVALID", message: "JSON 格式错误" }] });
      return;
    }
    stopStream();
    setBusy(true);
    setError(null);
    setExportPayload(null);
    try {
      const result = await submitStrategyBacktest({ spec: parsedSpec, ...DEFAULT_DATES });
      const initialTask: Task = {
        task_id: result.task_id,
        status: "pending",
        task_type: "strategy_backtest",
      };
      setStrategyTask(initialTask);
      closeStreamRef.current = streamTask(
        result.task_id,
        (task) => {
          setStrategyTask(task);
          if (task.status === "failed") setError(task.error ?? "策略任务失败");
          if (TERMINAL_STATUSES.has(task.status)) setBusy(false);
        },
        () => setBusy(false),
        (message) => {
          setError(message);
          if (!strategyTask || strategyTask.status === "pending") setBusy(false);
        },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "策略回测提交失败");
      setBusy(false);
    }
  }

  async function handleExport() {
    const strategyResult = taskResult?.strategy_result;
    if (!strategyResult) return;
    setExporting(true);
    setError(null);
    try {
      setExportPayload(await exportStrategyCandidate(strategyResult));
    } catch (err) {
      setError(err instanceof Error ? err.message : "策略导出失败");
    } finally {
      setExporting(false);
    }
  }

  async function handleOpenReport(reportUrl: string) {
    setCheckingReport(true);
    setError(null);
    try {
      const res = await authFetch(reportUrl);
      if (!res.ok) throw new Error(await parseError(res));
      window.open(getReportUrl(reportUrl), "_blank", "noopener,noreferrer");
    } catch (err) {
      setError(err instanceof Error ? err.message : "报告读取失败");
    } finally {
      setCheckingReport(false);
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">策略工作台</h2>
          <p className="text-sm text-gray-500">StrategySpec v1</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={selectedTemplate}
            onChange={(event) => setSelectedTemplate(event.target.value)}
            className="h-9 rounded-md border border-gray-300 bg-white px-3 text-sm"
          >
            {templates.map((template) => (
              <option key={template.id} value={template.id}>{template.name}</option>
            ))}
          </select>
          <button
            onClick={() => void handleValidate()}
            disabled={busy}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-gray-900 px-3 text-sm font-medium text-white disabled:opacity-50"
          >
            <CheckCircle2 className="h-4 w-4" />
            校验
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={submitDisabled}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-blue-600 px-3 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            回测
          </button>
        </div>
      </div>

      {selected && (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-gray-900">
            <FlaskConical className="h-4 w-4 text-blue-600" />
            {selected.description}
          </div>
          <div className="mt-2 text-xs text-gray-500">Risk: {selected.risk_label}</div>
        </div>
      )}

      {isGuest && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <AlertTriangle className="h-4 w-4" />
          请登录后提交策略回测
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <textarea
          value={specText}
          onChange={(event) => setSpecText(event.target.value)}
          spellCheck={false}
          className="min-h-[620px] rounded-lg border border-gray-200 bg-white p-4 font-mono text-xs leading-5 text-gray-800 outline-none focus:border-blue-500"
        />
        <aside className="space-y-3">
          {validation && (
            <div className={`rounded-lg border p-4 ${validation.is_valid ? "border-green-200 bg-green-50" : "border-red-200 bg-red-50"}`}>
              <div className={`flex items-center gap-2 text-sm font-medium ${validation.is_valid ? "text-green-700" : "text-red-700"}`}>
                {validation.is_valid ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
                {validation.is_valid ? "校验通过" : "校验失败"}
              </div>
              {!validation.is_valid && (
                <ul className="mt-3 space-y-2 text-xs text-red-700">
                  {validation.issues.map((issue, index) => (
                    <li key={`${issue.code}-${index}`}>
                      <span className="font-semibold">{issue.code}</span>
                      {issue.path ? ` @ ${issue.path}` : ""}: {issue.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {strategyTask && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-800">
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium">{strategyTask.status}</div>
                {!TERMINAL_STATUSES.has(strategyTask.status) && <Loader2 className="h-4 w-4 animate-spin" />}
              </div>
              <div className="mt-1 font-mono text-xs">{strategyTask.task_id}</div>
              {strategyTask.error && <div className="mt-2 text-red-700">{strategyTask.error}</div>}
            </div>
          )}

          {taskResult?.strategy_score && (
            <div className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="text-sm font-medium text-gray-900">评分</div>
              <div className="mt-3 flex items-end gap-3">
                <div className="text-3xl font-semibold text-gray-900">{formatValue(taskResult.strategy_score.score)}</div>
                <div className="pb-1 text-sm font-medium text-blue-700">{taskResult.strategy_score.grade}</div>
              </div>
            </div>
          )}

          {metricEntries.length > 0 && (
            <div className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="text-sm font-medium text-gray-900">指标</div>
              <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                {metricEntries.map(([key, value]) => (
                  <div key={key} className="rounded-md bg-gray-50 p-2">
                    <dt className="text-gray-500">{key}</dt>
                    <dd className="mt-1 font-mono text-gray-900">{formatValue(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {latestHoldings.length > 0 && (
            <div className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="text-sm font-medium text-gray-900">持仓</div>
              <div className="mt-3 overflow-hidden rounded-md border border-gray-100">
                <table className="w-full text-left text-xs">
                  <thead className="bg-gray-50 text-gray-500">
                    <tr>
                      <th className="px-2 py-2 font-medium">代码</th>
                      <th className="px-2 py-2 text-right font-medium">权重</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {latestHoldings.slice(0, 8).map((holding, index) => (
                      <tr key={`${holding.stock_code}-${index}`}>
                        <td className="px-2 py-2 font-mono text-gray-900">{holding.stock_code}</td>
                        <td className="px-2 py-2 text-right font-mono text-gray-900">{holdingWeight(holding)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {taskResult?.report_url && (
            <div className="flex gap-2">
              <button
                onClick={() => void handleOpenReport(taskResult.report_url as string)}
                disabled={checkingReport}
                className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-md border border-gray-300 bg-white px-3 text-sm font-medium text-gray-800 disabled:opacity-50"
              >
                {checkingReport ? <Loader2 className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
                报告
              </button>
              <button
                onClick={() => void handleExport()}
                disabled={exporting || !taskResult.strategy_result}
                className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-md bg-gray-900 px-3 text-sm font-medium text-white disabled:opacity-50"
              >
                {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                导出
              </button>
            </div>
          )}

          {exportPayload && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
              <div className="font-medium">{exportPayload.strategy_name}</div>
              <div className="mt-2 text-xs">signals: {exportPayload.signals.length}</div>
              <div className="mt-1 text-xs">data_end: {exportPayload.data_end}</div>
              <div className="mt-2 text-xs">{exportPayload.notice}</div>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>
          )}
        </aside>
      </div>
    </section>
  );
}
