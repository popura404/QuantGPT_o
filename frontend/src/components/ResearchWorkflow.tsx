import { useEffect, useRef, useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { evaluateResearchFactors, exportResearchStrategy, getResearchArtifact, getResearchEvaluation, getResearchRun, optimizeResearchSignal, prepareOfflineDemo, registerResearchHypothesis, runResearchStrategy, saveEvaluationToPool, type OfflineResearchDemo, type ResearchEvaluation, type ResearchRun } from "../api/research";
import { useResearchProject } from "../hooks/useResearchProject";

function display(value: unknown): string {
  return typeof value === "number" ? (Number.isFinite(value) ? value.toFixed(4) : "—") : String(value ?? "—");
}

function blockerLabel(value: unknown): string {
  const code = String(value);
  const reasons: Record<string, string> = { VALIDATION_PROFILE_INCOMPLETE: "验证证据尚未齐备", FINAL_TEST_REQUIRED: "还需预登记窗口中的最终测试", EVIDENCE_NOT_VERIFIED: "现有证据尚未验证" };
  const evidence: Record<string, string> = { strategy_identity: "策略身份", factor_lineage: "因子来源", replayable_data: "数据重放", simulation: "仿真契约", strategy_oos: "策略样本外表现", walk_forward: "滚动验证", costs: "费用敏感性", risk: "风险诊断", trial_correction: "多次试验校正" };
  if (code.startsWith("REQUIRED_EVIDENCE:")) return `缺少${evidence[code.split(":")[1]] ?? code.split(":")[1]}证据`;
  return reasons[code] ?? code;
}

function ExportDecision({ result }: { result: Record<string, unknown> }) {
  const validation = result.validation as Record<string, unknown> | undefined;
  const blockers = Array.isArray(validation?.blockers) ? validation.blockers : [];
  return <div role="status" className="rounded bg-amber-50 p-3 text-sm text-amber-900">
    <strong>{result.exported === true ? "已导出研究信号" : "导出被阻断"}</strong>
    {blockers.length > 0 && <ul className="mt-2 list-inside list-disc space-y-1 text-xs">{blockers.map((reason, index) => <li key={index}>{blockerLabel(reason)}</li>)}</ul>}
    <details className="mt-3 text-xs"><summary>服务端验证明细</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap">{JSON.stringify(validation ?? result, null, 2)}</pre></details>
  </div>;
}

function Metrics({ values }: { values?: Record<string, unknown> }) {
  const names: Record<string, string> = { sharpe: "Sharpe", sharpe_ratio: "Sharpe", long_short_sharpe: "多空诊断 Sharpe", cagr: "年化收益", annual_return: "年化收益", max_drawdown: "最大回撤", turnover: "换手率", ic_mean: "平均 IC", total_return: "累计收益" };
  const ratios = new Set(["cagr", "annual_return", "max_drawdown", "turnover", "total_return"]);
  return <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">{Object.entries(values ?? {}).filter(([key, value]) => key in names && typeof value === "number").map(([key, value]) => <div key={key} className="rounded bg-gray-50 p-3"><dt className="text-xs text-gray-500">{names[key]}</dt><dd className="mt-1 font-mono text-base text-gray-900">{ratios.has(key) && typeof value === "number" ? `${(value * 100).toFixed(2)}%` : display(value)}</dd></div>)}</dl>;
}

function editedDefinition(demo: OfflineResearchDemo, expression: string): Record<string, unknown> {
  // Only copy server-provided field contracts; unknown identifiers remain for
  // the server parser/capability check, never invent units or availability.
  const identifiers = new Set(expression.match(/[A-Za-z_][A-Za-z_0-9]*/g) ?? []);
  const catalog = demo.field_catalog ?? (demo.definitions[0].fields as Record<string, unknown>[] ?? []);
  return { ...demo.definitions[0], expression, fields: catalog.filter((field) => identifiers.has(String(field.name))) };
}

function PortfolioDecision({ result }: { result: Record<string, unknown> }) {
  const rows = Array.isArray(result.target_weights) ? result.target_weights as Record<string, unknown>[] : [];
  return <section aria-label="优化结果" className="space-y-3 rounded border border-blue-200 bg-blue-50 p-3">
    <h4 className="text-sm font-medium">{result.feasible === true ? "组合约束可行" : "组合优化被阻断"}</h4>
    {result.fallback_used === true && <p className="text-xs text-amber-800">优化器未给出可用解，服务端已回退到可行基准；请核对下方优化证据。</p>}
    {typeof result.error_code === "string" && <p className="text-xs text-amber-800">{display(result.error_code)}：{display(result.detail)}</p>}
    <p className="text-xs text-gray-600">使用已保存的真实信号与截至估计日的历史数据。优化结果仅供研究，不会解锁导出。</p>
    {result.feasible === true && <><p className="text-xs">现金权重：{(Number(result.cash_weight) * 100).toFixed(2)}%</p><table className="w-full text-left text-xs"><thead><tr><th>证券</th><th>目标权重</th></tr></thead><tbody>{rows.map((row) => <tr key={String(row.security_id)}><td className="py-1">{String(row.security_id)}</td><td>{(Number(row.target_weight) * 100).toFixed(2)}%</td></tr>)}</tbody></table></>}
    <details className="text-xs text-gray-500"><summary>约束与优化证据</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap">{JSON.stringify(result, null, 2)}</pre></details>
  </section>;
}

export default function ResearchWorkflow() {
  const projectId = useResearchProject();
  const [demo, setDemo] = useState<OfflineResearchDemo | null>(null);
  const [expression, setExpression] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [evaluations, setEvaluations] = useState<ResearchEvaluation[]>([]);
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [exportResult, setExportResult] = useState<Record<string, unknown> | null>(null);
  const [curve, setCurve] = useState<{ session: string; nav: number }[]>([]);
  const [optimization, setOptimization] = useState<Record<string, unknown> | null>(null);
  const [assetCap, setAssetCap] = useState(0.2);
  const [cashMax, setCashMax] = useState(0.5);
  const generation = useRef(0);
  const key = `quantgpt_research_workflow_${projectId ?? "none"}`;

  useEffect(() => {
    const current = ++generation.current;
    setDemo(null); setExpression(""); setHypothesis(""); setEvaluations([]); setRun(null); setRunId(""); setError(null); setNotice(null); setCurve([]); setOptimization(null); setExportResult(null); setBusy(false);
    if (!projectId) return;
    const saved = localStorage.getItem(key);
    if (!saved) return;
    try {
      const state = JSON.parse(saved) as { demo: OfflineResearchDemo | null; evaluationIds: string[]; runId?: string };
      setDemo(state.demo); setExpression(String(state.demo?.definitions[0]?.expression ?? ""));
      setBusy(true);
      Promise.all([Promise.all(state.evaluationIds.map((id) => getResearchEvaluation(projectId, id))), state.runId ? getResearchRun(projectId, state.runId) : Promise.resolve(null)])
        .then(([items, result]) => { if (generation.current === current) { setEvaluations(items); setRun(result); setRunId(result?.strategy_run_id ?? ""); } })
        .catch((err) => { if (generation.current === current) setError(err instanceof Error ? err.message : "研究记录恢复失败"); })
        .finally(() => { if (generation.current === current) setBusy(false); });
    } catch { localStorage.removeItem(key); }
    return () => { generation.current++; };
  }, [projectId, key]);

  function persist(nextDemo: OfflineResearchDemo | null, items: ResearchEvaluation[], result: ResearchRun | null) {
    localStorage.setItem(key, JSON.stringify({ demo: nextDemo, evaluationIds: items.map((item) => item.evaluation_id), runId: result?.strategy_run_id }));
  }

  async function action(work: (current: () => boolean) => Promise<void>) {
    const requestGeneration = generation.current;
    setBusy(true); setError(null); setNotice(null);
    try { await work(() => generation.current === requestGeneration); }
    catch (err) { if (generation.current === requestGeneration) setError(err instanceof Error ? err.message : "研究操作失败"); }
    finally { if (generation.current === requestGeneration) setBusy(false); }
  }

  if (!projectId) return <p className="rounded border border-blue-200 bg-blue-50 p-5 text-sm text-blue-800">先选择或创建研究项目，再开始可追溯的因子评价。</p>;

  return <section aria-label="项目研究流程" className="space-y-5">
    <div><h2 className="text-lg font-semibold">项目研究流程</h2><p className="mt-1 text-sm text-gray-500">准备数据 → 评价因子 → 收藏证据 → 组合策略 → 检查导出条件</p></div>
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">离线演示使用固定种子生成的合成行情，仅验证研究流程和契约。它不是实际美股数据，不能据此判断投资有效性。</div>
    {error && <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    {notice && <p role="status" className="text-sm text-blue-700">{notice}</p>}
    <div className="rounded-lg border bg-white p-4">
      <button disabled={busy} className="rounded bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50" onClick={() => void action(async (current) => {
        const prepared = await prepareOfflineDemo(projectId);
        if (!current()) return;
        setDemo(prepared); setExpression(String(prepared.definitions[0].expression)); setEvaluations([]); setRun(null); setCurve([]); setOptimization(null); setExportResult(null); persist(prepared, [], null);
      })}>准备离线合成数据</button>
      {demo && <div className="mt-4 space-y-3">
        <p className="text-xs text-gray-500">数据已冻结；评价身份包含实际内容哈希、研究窗口、费用和语义版本。</p>
        <label className="block text-sm text-gray-700">因子表达式<input aria-label="研究因子表达式" className="mt-1 block w-full rounded border p-2 font-mono text-sm" value={expression} disabled={busy} onChange={(event) => { setExpression(event.target.value); setEvaluations([]); setRun(null); setCurve([]); setOptimization(null); setExportResult(null); }} /></label>
        <label className="block text-sm text-gray-700">经济假设（可选，在评价前登记）<textarea aria-label="经济假设" className="mt-1 block w-full rounded border p-2 text-sm" maxLength={10000} rows={2} disabled={busy || evaluations.length > 0} value={hypothesis} onChange={(event) => setHypothesis(event.target.value)} placeholder="说明预期机制与失效条件；评价后不能改写已登记假设。" /></label>
        <button disabled={busy || !expression.trim()} className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50" onClick={() => void action(async (current) => {
          const prepared = { ...demo, definitions: [editedDefinition(demo, expression)] };
          if (hypothesis.trim()) await registerResearchHypothesis(projectId, prepared.definitions[0], hypothesis.trim());
          const response = await evaluateResearchFactors(projectId, prepared.definitions, prepared.config);
          if (!current()) return;
          setDemo(prepared); setEvaluations(response.evaluations); setRun(null); persist(prepared, response.evaluations, null);
        })}>评价因子</button>
        <details className="text-xs text-gray-500"><summary>数据与评价契约</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap">{JSON.stringify(demo.config, null, 2)}</pre></details>
      </div>}
    </div>
    {busy && <p role="status" className="text-sm text-blue-700">正在读取或计算服务端研究结果…</p>}
    {evaluations.map((item, index) => <article key={item.evaluation_id} className="space-y-3 rounded-lg border bg-white p-4">
      <h3 className="font-medium">因子评价 · {item.status}</h3><p className="text-xs text-gray-500">证据：{item.evidence_status} · 结论：{item.summary?.research_decision ?? "尚未验证"}</p>
      {item.failure_reason && <p role="alert" className="text-sm text-red-700">{item.failure_reason}</p>}
      <Metrics values={item.summary?.metrics} />
      {item.summary?.blockers?.map((blocker, n) => <p key={n} className="text-xs text-amber-800">阻断：{blockerLabel(blocker)}</p>)}
      <button disabled={busy || !demo} className="rounded border px-3 py-2 text-sm disabled:opacity-50" onClick={() => void action(async (current) => { if (!demo) return; await saveEvaluationToPool(projectId, item, demo.definitions[index], demo.config); if (current()) setNotice("已收藏到项目共同因子池，保留服务端评价引用。"); })}>收藏评价到共同池</button>
      <details className="text-xs text-gray-500"><summary>评价引用</summary><p className="break-all">{item.evaluation_id}</p><p className="break-all">{item.evaluation_hash}</p></details>
    </article>)}
    {demo && evaluations.length > 0 && <button disabled={busy || evaluations.some((item) => item.status === "rejected")} className="rounded bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50" onClick={() => void action(async (current) => {
      const templateFactors = demo.strategy_template.factors as Record<string, unknown>[];
      const window = demo.config.window as Record<string, unknown>;
      const spec = { ...demo.strategy_template, factors: templateFactors.map((factor, index) => ({ ...factor, expression: demo.definitions[index].expression })), factor_evaluations: evaluations.map((item) => ({ evaluation_id: item.evaluation_id, project_id: projectId, evaluation_hash: item.evaluation_hash, definition_hash: item.definition_hash, backend: item.backend, scope: window.phase })) };
      const result = await runResearchStrategy(projectId, spec, demo.config);
      if (!current()) return;
      setRun(result); setRunId(result.strategy_run_id); setExportResult(null); setCurve([]); setOptimization(null); persist(demo, evaluations, result);
    })}>使用评价证据运行策略</button>}
    <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); void action(async (current) => { const result = await getResearchRun(projectId, runId); if (current()) { setRun(result); setCurve([]); setOptimization(null); setExportResult(null); persist(demo, evaluations, result); } }); }}><input aria-label="服务端策略运行 ID" value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="服务端策略运行 ID" className="min-w-64 flex-1 rounded border p-2 text-sm" /><button disabled={busy || !runId} className="rounded border px-3 text-sm disabled:opacity-50">读取策略记录</button></form>
    {run && <article aria-label="项目策略结果" className="space-y-3 rounded-lg border bg-white p-4">
      <h3 className="font-medium">策略结果 · {run.evidence_status}</h3><p className="text-xs text-gray-500">研究结论：{run.research_decision}</p><Metrics values={run.metrics} />
      {run.blockers.map((blocker, index) => <p key={index} className="text-sm text-amber-800">阻断：{blockerLabel(blocker)}</p>)}
      <p className="text-xs text-gray-500">只有服务端保存的证据可解锁导出；页面指标和收藏状态均不构成验证通过。</p>
      <div className="flex gap-2"><button disabled={busy} className="rounded border px-3 py-2 text-sm disabled:opacity-50" onClick={() => void action(async (current) => {
        const ref = run.artifacts.find((item) => item.kind === "returns"); if (!ref) throw new Error("收益明细尚不存在");
        const data = await getResearchArtifact(projectId, ref.artifact_id); let nav = 1;
        const points = data.payload.map((row) => { nav *= 1 + Number(row.return); return { session: String(row.session).slice(0, 10), nav }; });
        if (current()) setCurve([{ session: "初始现金", nav: 1 }, ...points]);
      })}>加载净值曲线</button><button disabled={busy} className="rounded border px-3 py-2 text-sm disabled:opacity-50" onClick={() => void action(async (current) => { const result = await exportResearchStrategy(projectId, run.strategy_run_id); if (current()) setExportResult(result); })}>检查并申请研究导出</button></div>
      {curve.length > 0 && <div className="h-64"><ResponsiveContainer width="100%" height="100%"><LineChart data={curve}><XAxis dataKey="session" minTickGap={60} /><YAxis domain={["auto", "auto"]} /><Tooltip /><Line type="linear" dataKey="nav" name="净值" stroke="#2563eb" dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>}
      {exportResult && <ExportDecision result={exportResult} />}
      <section className="space-y-3 border-t pt-4"><h4 className="text-sm font-medium">使用保存信号优化组合</h4>
        <div className="flex flex-wrap gap-3"><label className="text-xs text-gray-500">单股权重上限 (0–1)<input aria-label="优化单股权重上限" disabled={busy} type="number" min="0.01" max="1" step="0.01" value={assetCap} onChange={(event) => { setAssetCap(Number(event.target.value)); setOptimization(null); }} className="mt-1 block rounded border p-2 text-sm" /></label><label className="text-xs text-gray-500">现金权重上限 (0–1)<input aria-label="优化现金权重上限" disabled={busy} type="number" min="0" max="1" step="0.05" value={cashMax} onChange={(event) => { setCashMax(Number(event.target.value)); setOptimization(null); }} className="mt-1 block rounded border p-2 text-sm" /></label></div>
        <button disabled={busy || !run.signal_ref || assetCap <= 0 || assetCap > 1 || cashMax < 0 || cashMax > 1} className="rounded border px-3 py-2 text-sm disabled:opacity-50" onClick={() => void action(async (current) => {
          if (!run.signal_ref) throw new Error("服务端尚无可追溯信号");
          const window = run.config.window as Record<string, unknown>;
          const result = await optimizeResearchSignal(projectId, run.signal_ref, { asof_session: window.end_session, max_asset_weight: assetCap, cash_max: cashMax });
          if (current()) setOptimization(result);
        })}>优化保存的信号</button>
        {optimization && <PortfolioDecision result={optimization} />}
      </section>
    </article>}
  </section>;
}
