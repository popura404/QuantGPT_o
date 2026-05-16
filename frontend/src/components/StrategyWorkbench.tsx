import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, FlaskConical, Play, RefreshCw, XCircle } from "lucide-react";
import { instantiateStrategyTemplate, listStrategyTemplates, submitStrategyBacktest, validateStrategySpec } from "../api/strategy";
import type { StrategyTemplateSummary, StrategyValidationResult } from "../types/strategy";

const DEFAULT_DATES = {
  start_date: "2024-01-02",
  end_date: "2024-03-29",
  benchmark: "hs300",
};

export default function StrategyWorkbench() {
  const [templates, setTemplates] = useState<StrategyTemplateSummary[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState("momentum_top_n_v1");
  const [specText, setSpecText] = useState("");
  const [validation, setValidation] = useState<StrategyValidationResult | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const parsedSpec = useMemo(() => {
    try {
      return JSON.parse(specText || "{}") as Record<string, unknown>;
    } catch {
      return null;
    }
  }, [specText]);

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
    setBusy(true);
    instantiateStrategyTemplate(selectedTemplate)
      .then((spec) => {
        setSpecText(JSON.stringify(spec, null, 2));
        setValidation(null);
        setTaskId(null);
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
    if (!parsedSpec) {
      setValidation({ is_valid: false, issues: [{ code: "JSON_INVALID", message: "JSON 格式错误" }] });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await submitStrategyBacktest({ spec: parsedSpec, ...DEFAULT_DATES });
      setTaskId(result.task_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "策略回测提交失败");
    } finally {
      setBusy(false);
    }
  }

  const selected = templates.find((template) => template.id === selectedTemplate);

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
            disabled={busy}
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

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <textarea
          value={specText}
          onChange={(event) => setSpecText(event.target.value)}
          spellCheck={false}
          className="min-h-[560px] rounded-lg border border-gray-200 bg-white p-4 font-mono text-xs leading-5 text-gray-800 outline-none focus:border-blue-500"
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

          {taskId && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-700">
              <div className="font-medium">已提交</div>
              <div className="mt-1 font-mono text-xs">{taskId}</div>
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
