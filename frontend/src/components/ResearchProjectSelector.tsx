import { useEffect, useState } from "react";
import { createResearchProject, listResearchProjects, type ResearchProject } from "../api/research";
import { useAuth } from "../contexts/AuthContext";
import { setResearchProjectId, useResearchProject } from "../hooks/useResearchProject";

export default function ResearchProjectSelector() {
  const { isGuest, user } = useAuth();
  const projectId = useResearchProject();
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [name, setName] = useState("");
  const [market, setMarket] = useState("us");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isGuest) return;
    let active = true;
    setBusy(true);
    listResearchProjects().then((items) => {
      if (!active) return;
      setProjects(items);
      if (projectId && !items.some((project) => project.id === projectId)) setResearchProjectId(null);
    }).catch((err) => { if (active) setError(err instanceof Error ? err.message : "项目读取失败"); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [isGuest, user?.id]);

  async function create() {
    if (!name.trim()) return;
    setBusy(true); setError(null);
    try {
      const project = await createResearchProject(name.trim(), market);
      setProjects((previous) => [...previous, project]);
      setResearchProjectId(project.id); setCreating(false); setName("");
    } catch (err) { setError(err instanceof Error ? err.message : "项目创建失败"); }
    finally { setBusy(false); }
  }

  if (isGuest) return null;
  return <section aria-label="研究项目" className="mx-auto max-w-7xl px-6 py-3">
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 bg-white p-3">
      <label className="text-xs font-medium text-gray-700">研究项目
        <select aria-label="研究项目选择" value={projectId ?? ""} disabled={busy} onChange={(event) => { setResearchProjectId(event.target.value || null); setError(null); }} className="ml-2 rounded border border-gray-200 px-2 py-1.5 text-sm">
          <option value="">个人旧收藏</option>
          {projects.map((project) => <option key={project.id} value={project.id}>{project.name} · {project.market}</option>)}
        </select>
      </label>
      <button type="button" onClick={() => setCreating(!creating)} className="text-xs font-medium text-blue-700">新建研究项目</button>
      <span className="text-xs text-gray-500">{projectId ? "网页与 MCP 共用此项目因子池；所有访问重新检查成员权限" : "选择项目后使用共同因子池；旧收藏仍保留"}</span>
      {creating && <form onSubmit={(event) => { event.preventDefault(); void create(); }} className="flex w-full flex-wrap gap-2">
        <input aria-label="项目名称" placeholder="项目名称" value={name} maxLength={200} onChange={(event) => setName(event.target.value)} className="rounded border px-2 py-1.5 text-sm" />
        <select aria-label="项目市场" value={market} onChange={(event) => setMarket(event.target.value)} className="rounded border px-2 text-sm"><option value="us">美股</option><option value="a_share">A 股</option></select>
        <button type="submit" disabled={busy || !name.trim()} className="rounded bg-blue-600 px-3 py-1.5 text-xs text-white disabled:opacity-50">创建并选择</button>
      </form>}
      {error && <p role="alert" className="w-full text-xs text-red-700">{error}</p>}
    </div>
  </section>;
}
