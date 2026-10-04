import { useState, useEffect, useCallback, useRef } from "react";
import { Star, Trash2, ExternalLink, RefreshCw } from "lucide-react";
import { useColorMode } from "../contexts/ColorModeContext";
import type { SavedFactor } from "../api/factorLibrary";
import { fetchFactors, deleteFactor, updateFactor } from "../api/factorLibrary";
import { getResearchEvaluation, type ResearchEvaluation } from "../api/research";
import { useResearchProject } from "../hooks/useResearchProject";
import ReportLink from "./ReportLink";

function number(value: unknown, percentage = false): string {
  return typeof value === "number" && Number.isFinite(value) ? (percentage ? `${(value * 100).toFixed(1)}%` : value.toFixed(2)) : "—";
}

export default function FactorLibrary() {
  const { isDark } = useColorMode();
  const projectId = useResearchProject();
  const [factors, setFactors] = useState<SavedFactor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [selected, setSelected] = useState<SavedFactor | null>(null);
  const [evaluation, setEvaluation] = useState<ResearchEvaluation | null>(null);
  const revision = useRef(0);

  const load = useCallback(async () => {
    const current = ++revision.current;
    setLoading(true); setError(null); setFactors([]); setSelected(null); setEvaluation(null);
    try {
      const data = await fetchFactors(projectId);
      if (revision.current === current) setFactors(data);
    } catch (err) { if (revision.current === current) setError(err instanceof Error ? err.message : "因子池读取失败"); }
    finally { if (revision.current === current) setLoading(false); }
  }, [projectId]);

  useEffect(() => { void load(); return () => { revision.current++; }; }, [load]);

  async function remove(factor: SavedFactor) {
    if (!window.confirm(projectId ? "删除此项目共享因子条目？所有项目成员将不可见。" : "删除此收藏？")) return;
    const current = revision.current;
    try { await deleteFactor(factor.id, projectId); if (current === revision.current) setFactors((items) => items.filter((item) => item.id !== factor.id)); }
    catch (err) { if (current === revision.current) setError(err instanceof Error ? err.message : "删除失败"); }
  }

  async function favorite(factor: SavedFactor) {
    const tags = factor.tags.includes("favorite") ? factor.tags.filter((tag) => tag !== "favorite") : [...factor.tags, "favorite"];
    const current = revision.current;
    try { const updated = await updateFactor(factor.id, { tags }, projectId); if (current === revision.current) setFactors((items) => items.map((item) => item.id === factor.id ? updated : item)); }
    catch (err) { if (current === revision.current) setError(err instanceof Error ? err.message : "收藏更新失败"); }
  }

  async function inspect(factor: SavedFactor) {
    setSelected(factor); setEvaluation(null); setError(null);
    const current = ++revision.current;
    if (projectId && factor.evaluation_id) {
      try { const result = await getResearchEvaluation(projectId, factor.evaluation_id); if (revision.current === current) setEvaluation(result); }
      catch (err) { if (revision.current === current) setError(err instanceof Error ? err.message : "评价证据读取失败"); }
    }
  }

  const shown = favoritesOnly && projectId ? factors.filter((factor) => factor.tags.includes("favorite")) : factors;
  return <section aria-label="共同因子池" className="space-y-3">
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs font-medium text-gray-500">{projectId ? "项目共同因子池" : "个人旧收藏"} · {shown.length}</span>
      <button type="button" aria-label="刷新因子池" onClick={() => void load()} disabled={loading} className="text-gray-500"><RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /></button>
    </div>
    {projectId && <label className="flex items-center gap-2 text-xs text-gray-500"><input type="checkbox" checked={favoritesOnly} onChange={(event) => setFavoritesOnly(event.target.checked)} />仅看项目收藏</label>}
    {error && <p role="alert" className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</p>}
    {loading && <p className="py-4 text-center text-xs text-gray-500">加载中…</p>}
    {!loading && shown.length === 0 && <p className="py-6 text-center text-xs text-gray-500">{projectId ? "项目尚无因子；可从回测结果收藏，或通过同项目 MCP 保存。" : "旧收藏为空；可选择研究项目查看共同因子池。"}</p>}
    {shown.map((factor) => <article key={factor.id} className={`rounded-lg border border-gray-200 p-3 ${isDark ? "bg-gray-900" : "bg-white"}`}>
      <button type="button" onClick={() => void inspect(factor)} className="block w-full text-left" title="查看因子与评价证据">
        {factor.name && <span className="mb-1 block text-xs font-medium text-gray-600">{factor.name}</span>}
        <code className="block break-all text-xs text-blue-700">{factor.expression}</code>
      </button>
      <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-gray-500"><span>Sharpe {number(factor.metrics?.sharpe)}</span><span>年收益 {number(factor.metrics?.cagr, true)}</span><span>回撤 {number(factor.metrics?.max_drawdown, true)}</span></div>
      <p className="mt-1 text-[10px] text-gray-500">{factor.market ?? "未记录市场"} · {factor.pool_status ?? "历史收藏"} · {factor.evaluation_id ? "评价证据待服务端核对" : "历史结果需重算"}</p>
      <div className="mt-2 flex gap-3">
        {projectId && <button type="button" aria-label={factor.tags.includes("favorite") ? "取消项目收藏" : "收藏到项目"} onClick={() => void favorite(factor)}><Star className={`h-3.5 w-3.5 ${factor.tags.includes("favorite") ? "fill-amber-400 text-amber-500" : "text-gray-400"}`} /></button>}
        {factor.report_url && <ReportLink reportUrl={factor.report_url} title="查看报告"><ExternalLink className="h-3.5 w-3.5 text-gray-400" /></ReportLink>}
        <button type="button" aria-label="删除因子" onClick={() => void remove(factor)}><Trash2 className="h-3.5 w-3.5 text-gray-400" /></button>
      </div>
    </article>)}
    {factors.length === 200 && <p className="text-xs text-gray-500">当前显示前 200 条。</p>}
    {selected && <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-gray-700">
      <div className="mb-2 flex justify-between"><strong>因子详情</strong><button onClick={() => setSelected(null)}>关闭</button></div>
      <p className="break-all">{selected.expression}</p>
      <p className="mt-2">{selected.note || selected.main_reason || "尚无研究说明"}</p>
      <p className="mt-2">证据状态：{evaluation?.evidence_status ?? selected.evidence_status ?? "legacy_unverified"}</p>
      <p>研究结论：{evaluation?.summary?.research_decision ?? "尚未验证"}</p>
      {evaluation?.summary?.blockers?.map((blocker, index) => <p key={index} className="mt-1 text-amber-800">{typeof blocker === "string" ? blocker : JSON.stringify(blocker)}</p>)}
      <details className="mt-2"><summary>版本与引用</summary><p className="break-all">定义：{selected.definition_hash ?? "历史定义"}</p><p className="break-all">评价：{selected.evaluation_id ?? "无可信评价"}</p></details>
    </div>}
  </section>;
}
