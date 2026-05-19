import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Play, RefreshCw } from "lucide-react";
import { streamTask } from "../api/client";
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

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled", "iteration_completed"]);

function isStrategyTaskResult(result: Task["result"]): result is StrategyBacktestTaskResult {
  return Boolean(result && typeof result === "object" && "strategy_result" in result);
}

export default function StrategyWorkbench() {
  const { isGuest } = useAuth();
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

  const parsedSpec = useMemo(() => {
    try {
      return JSON.parse(specText || "{}") as Record<string, unknown>;
    } catch {
      return null;
    }
  }, [specText]);

  const taskResult = isStrategyTaskResult(strategyTask?.result) ? strategyTask.result : null;

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
        if (items[0]) setSelectedTemplate(items[0].id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "模板加载失败"));
    listStrategyMarkets().then(setMarketMeta).catch(() => {});
    listStrategyDataFields("a_share").then(setDataFields).catch(() => {});
  }, []);

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
          <p className="text-sm text-gray-500">StrategySpec v1 structured workflow</p>
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

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="space-y-4">
          <StrategySpecEditor value={specText} onChange={setSpecText} />
        </div>
        <aside className="space-y-4">
          {busy && !strategyTask && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-700">
              <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
              处理中
            </div>
          )}
          <StrategyParameterForm
            spec={parsedSpec}
            onChange={(next) => {
              setSpecText(JSON.stringify(next, null, 2));
              setValidation(null);
            }}
          />
          <StrategyValidationPanel validation={validation} />
          {strategyTask && <TaskProgressPanel task={strategyTask} />}
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
              <pre className="mt-2 max-h-52 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-gray-700">
                {JSON.stringify({ markets: marketMeta, data_fields: dataFields }, null, 2)}
              </pre>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
