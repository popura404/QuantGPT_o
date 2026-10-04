import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Play, RefreshCw } from "lucide-react";
import { streamTask, cancelTask, getTask } from "../api/client";
import { useResearchProject } from "../hooks/useResearchProject";
import {
  exportStrategyCandidate,
  getStrategySpec,
  instantiateStrategyTemplate,
  listStrategyDataFields,
  listStrategyMarkets,
  listStrategyRuns,
  listStrategySpecs,
  listStrategyTemplates,
  saveStrategyRun,
  saveStrategySpec,
  submitStrategyBacktest,
  validateStrategySpec,
} from "../api/strategy";
import { useAuth } from "../contexts/AuthContext";
import type { Task } from "../types/backtest";
import { TERMINAL_TASK_STATUSES as TERMINAL_STATUSES } from "../types/backtest";
import type {
  StrategyBacktestTaskResult,
  StrategyExportPayload,
  StrategyRunRecord,
  StrategySpecRecord,
  StrategyTemplateSummary,
  StrategyValidationResult,
} from "../types/strategy";
import StrategyDiagnosticsPanel from "./strategy/StrategyDiagnosticsPanel";
import StrategyParameterForm from "./strategy/StrategyParameterForm";
import StrategyResultPanel from "./strategy/StrategyResultPanel";
import StrategyRunHistory from "./strategy/StrategyRunHistory";
import StrategySpecEditor from "./strategy/StrategySpecEditor";
import StrategySpecLibrary from "./strategy/StrategySpecLibrary";
import StrategyTemplatePicker from "./strategy/StrategyTemplatePicker";
import StrategyValidationPanel from "./strategy/StrategyValidationPanel";
import TaskProgressPanel from "./tasks/TaskProgressPanel";

const DEFAULT_DATES = {
  start_date: "2024-01-02",
  end_date: "2024-03-29",
  benchmark: "hs300",
};

function isStrategyTaskResult(result: Task["result"]): result is StrategyBacktestTaskResult {
  return Boolean(result && typeof result === "object" && "strategy_result" in result);
}

export default function StrategyWorkbench() {
  const { isGuest } = useAuth();
  const projectId = useResearchProject();
  const [dates, setDates] = useState(DEFAULT_DATES);
  const [templates, setTemplates] = useState<StrategyTemplateSummary[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [specText, setSpecText] = useState("");
  const [validation, setValidation] = useState<StrategyValidationResult | null>(null);
  const [strategyTask, setStrategyTask] = useState<Task | null>(null);
  const [exportPayload, setExportPayload] = useState<StrategyExportPayload | null>(null);
  const [specs, setSpecs] = useState<StrategySpecRecord[]>([]);
  const [runs, setRuns] = useState<StrategyRunRecord[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState<string | null>(null);
  const [marketMeta, setMarketMeta] = useState<Record<string, unknown> | null>(null);
  const [dataFields, setDataFields] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [savingRun, setSavingRun] = useState(false);
  const closeStreamRef = useRef<(() => void) | null>(null);
  const taskStorageKey = `quantgpt_strategy_task_${projectId ?? "personal"}`;

  const parsedSpec = useMemo(() => {
    try {
      return JSON.parse(specText || "{}") as Record<string, unknown>;
    } catch {
      return null;
    }
  }, [specText]);

  const taskResult = isStrategyTaskResult(strategyTask?.result) ? strategyTask.result : null;
  const markets = Array.isArray(marketMeta?.markets) ? marketMeta.markets as Record<string, unknown>[] : [];
  const selectedMarket = markets.find((market) => market.market === parsedSpec?.market);
  const benchmarks = Array.isArray(selectedMarket?.benchmarks) ? selectedMarket.benchmarks as string[] : [];
  const fields = Array.isArray(dataFields?.data_fields) ? dataFields.data_fields as Record<string, unknown>[] : [];

  function stopStream() {
    closeStreamRef.current?.();
    closeStreamRef.current = null;
  }

  async function refreshLibrary() {
    setLibraryLoading(true);
    try {
      const [loadedSpecs, loadedRuns] = await Promise.all([
        isGuest ? Promise.resolve([]) : listStrategySpecs(),
        isGuest ? Promise.resolve([]) : listStrategyRuns(selectedStrategyId),
      ]);
      setSpecs(loadedSpecs);
      setRuns(loadedRuns);
    } catch (err) {
      setError(err instanceof Error ? err.message : "策略库读取失败");
    } finally {
      setLibraryLoading(false);
    }
  }

  useEffect(() => {
    return () => stopStream();
  }, []);

  useEffect(() => {
    listStrategyTemplates()
      .then((items) => {
        setTemplates(items);
        if (items[0] && !localStorage.getItem(taskStorageKey)) setSelectedTemplate(items[0].id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "模板加载失败"));
    listStrategyMarkets().then(setMarketMeta).catch(() => {});
  }, []);

  useEffect(() => {
    const market = parsedSpec?.market;
    if (typeof market !== "string") return;
    let current = true;
    setDataFields(null);
    listStrategyDataFields(market).then((value) => { if (current) setDataFields(value); })
      .catch((err) => { if (current) setError(err instanceof Error ? err.message : "字段能力读取失败"); });
    return () => { current = false; };
  }, [parsedSpec?.market]);

  useEffect(() => {
    if (benchmarks.length && !benchmarks.includes(dates.benchmark)) setDates((previous) => ({ ...previous, benchmark: String(selectedMarket?.default_benchmark ?? benchmarks[0]) }));
  }, [selectedMarket]);

  useEffect(() => {
    const taskId = localStorage.getItem(taskStorageKey);
    if (!taskId || isGuest) return;
    let current = true;
    getTask(taskId).then((task) => {
      if (!current) return;
      setStrategyTask(task);
      if (isStrategyTaskResult(task.result) && task.result.strategy_result?.spec) setSpecText(JSON.stringify(task.result.strategy_result.spec, null, 2));
      if (!TERMINAL_STATUSES.has(String(task.status))) {
        setBusy(true);
        closeStreamRef.current = streamTask(taskId, (updated) => { if (current) setStrategyTask(updated); }, () => { if (current) setBusy(false); }, (message) => { if (current) { setError(message); setBusy(false); } });
      }
    }).catch((err) => { if (current) { setError(err instanceof Error ? err.message : "任务恢复失败"); localStorage.removeItem(taskStorageKey); } });
    return () => { current = false; stopStream(); };
  }, [taskStorageKey, isGuest]);

  useEffect(() => {
    void refreshLibrary();
  }, [isGuest, selectedStrategyId]);

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
        setSelectedStrategyId(null);
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
    if (!dates.start_date || !dates.end_date || dates.start_date >= dates.end_date) {
      setError("请填写有效研究日期，开始日期必须早于结束日期。");
      return;
    }
    stopStream();
    setBusy(true);
    setError(null);
    setExportPayload(null);
    try {
      const result = await submitStrategyBacktest({ spec: parsedSpec, ...dates });
      localStorage.setItem(taskStorageKey, result.task_id);
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
          if (TERMINAL_STATUSES.has(String(task.status))) setBusy(false);
        },
        () => setBusy(false),
        (message) => {
          setError(message);
          setBusy(false);
        },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "策略回测提交失败");
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!strategyTask) return;
    try { await cancelTask(strategyTask.task_id); setStrategyTask(await getTask(strategyTask.task_id)); setBusy(false); }
    catch (err) { setError(err instanceof Error ? err.message : "取消请求失败"); }
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

  async function handleSaveSpec(name: string | null, tags: string[]) {
    if (!parsedSpec) return;
    setLibraryLoading(true);
    setError(null);
    try {
      const saved = await saveStrategySpec(parsedSpec, name, tags);
      setSelectedStrategyId(saved.id);
      await refreshLibrary();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Spec 保存失败");
    } finally {
      setLibraryLoading(false);
    }
  }

  async function handleLoadSpec(strategyId: string) {
    setLibraryLoading(true);
    setError(null);
    try {
      const record = await getStrategySpec(strategyId);
      setSelectedStrategyId(record.id);
      setSpecText(JSON.stringify(record.spec, null, 2));
      setValidation(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Spec 读取失败");
    } finally {
      setLibraryLoading(false);
    }
  }

  async function handleSaveRun() {
    const strategyResult = taskResult?.strategy_result;
    if (!strategyResult) return;
    setSavingRun(true);
    setError(null);
    try {
      await saveStrategyRun(
        strategyResult,
        selectedStrategyId,
        strategyTask?.task_id,
        taskResult.report_url,
        null,
        exportPayload,
      );
      await refreshLibrary();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run 保存失败");
    } finally {
      setSavingRun(false);
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">策略工作台</h2>
          <p className="text-sm text-gray-500">研究配置、持仓回测与验证证据</p>
        </div>
        <div className="flex items-center gap-2">
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
            disabled={busy || isGuest}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-blue-600 px-3 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            回测
          </button>
        </div>
      </div>

      {isGuest && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <AlertTriangle className="h-4 w-4" />
          请登录后提交策略回测
        </div>
      )}
      {error && <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>}

      <StrategyTemplatePicker templates={templates} selectedId={selectedTemplate} onSelect={setSelectedTemplate} />

      <section aria-label="研究范围" className="grid gap-3 rounded-lg border border-gray-200 bg-white p-4 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-xs text-gray-500">开始日期<input aria-label="研究开始日期" type="date" value={dates.start_date} disabled={busy} onChange={(event) => setDates({ ...dates, start_date: event.target.value })} className="mt-1 block w-full rounded border border-gray-200 p-2 text-sm text-gray-800" /></label>
        <label className="text-xs text-gray-500">结束日期<input aria-label="研究结束日期" type="date" value={dates.end_date} disabled={busy} onChange={(event) => setDates({ ...dates, end_date: event.target.value })} className="mt-1 block w-full rounded border border-gray-200 p-2 text-sm text-gray-800" /></label>
        <label className="text-xs text-gray-500">市场<select aria-label="策略市场" value={String(parsedSpec?.market ?? "")} disabled={busy || !parsedSpec} onChange={(event) => {
          const capability = markets.find((item) => item.market === event.target.value);
          const universes = Array.isArray(capability?.universes) ? capability.universes : [];
          setSpecText(JSON.stringify({ ...parsedSpec, schema_version: "strategy_spec/v1", market: event.target.value, universe: universes[0] ?? parsedSpec?.universe }, null, 2)); setValidation(null);
        }} className="mt-1 block w-full rounded border border-gray-200 p-2 text-sm text-gray-800"><option value="" disabled>选择模板或市场</option>{markets.map((market) => <option key={String(market.market)} value={String(market.market)}>{String(market.market)}</option>)}</select></label>
        <label className="text-xs text-gray-500">基准<select aria-label="策略基准" value={dates.benchmark} disabled={busy} onChange={(event) => setDates({ ...dates, benchmark: event.target.value })} className="mt-1 block w-full rounded border border-gray-200 p-2 text-sm text-gray-800">{!benchmarks.includes(dates.benchmark) && <option value={dates.benchmark}>{dates.benchmark}</option>}{benchmarks.map((benchmark) => <option key={benchmark} value={benchmark}>{benchmark}</option>)}</select></label>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="space-y-4">
          <StrategyParameterForm spec={parsedSpec} onChange={(next) => { setSpecText(JSON.stringify(next, null, 2)); setValidation(null); }} />
          <details className="rounded-lg border border-gray-200 bg-white p-4"><summary className="cursor-pointer text-sm text-gray-600">高级：查看或编辑策略 JSON</summary><StrategySpecEditor value={specText} onChange={setSpecText} /></details>
        </div>
        <aside className="space-y-4">
          {busy && !strategyTask && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-700">
              <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
              处理中
            </div>
          )}
          <StrategyValidationPanel validation={validation} />
          {strategyTask && <TaskProgressPanel task={strategyTask} onCancel={() => void handleCancel()} />}
          <StrategyResultPanel
            result={taskResult}
            exportPayload={exportPayload}
            exporting={exporting}
            savingRun={savingRun}
            onExport={() => void handleExport()}
            onSaveRun={() => void handleSaveRun()}
          />
          <StrategyDiagnosticsPanel result={taskResult?.strategy_result ?? null} spec={parsedSpec} />
          <StrategySpecLibrary
            specs={specs}
            loading={libraryLoading}
            onRefresh={() => void refreshLibrary()}
            onSave={(name, tags) => void handleSaveSpec(name, tags)}
            onLoad={(strategyId) => void handleLoadSpec(strategyId)}
          />
          <StrategyRunHistory runs={runs} loading={libraryLoading} onRefresh={() => void refreshLibrary()} />
          {(marketMeta || dataFields) && (
            <div className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="text-sm font-semibold text-gray-900">市场与字段</div>
              <p className="mt-1 text-xs text-gray-500">{String(parsedSpec?.market ?? "未选择市场")} · 字段是否可用于研究，以服务端能力检查为准</p>
              <div className="mt-2 max-h-64 overflow-auto"><table className="w-full text-left text-xs"><thead><tr><th className="py-2">字段</th><th>单位</th><th>能力状态</th></tr></thead><tbody>{fields.map((field) => <tr key={String(field.name)} className="border-t border-gray-100"><td className="py-2 font-mono">{String(field.name)}</td><td>{String(field.unit ?? "未知")}</td><td>{String(field.status ?? field.availability ?? "unknown")}</td></tr>)}</tbody></table></div>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
