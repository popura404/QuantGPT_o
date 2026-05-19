import { useState } from "react";
import { RefreshCw, Save } from "lucide-react";
import type { StrategySpecRecord } from "../../types/strategy";

interface Props {
  specs: StrategySpecRecord[];
  loading: boolean;
  onRefresh: () => void;
  onSave: (name: string | null, tags: string[]) => void;
  onLoad: (strategyId: string) => void;
}

export default function StrategySpecLibrary({ specs, loading, onRefresh, onSave, onLoad }: Props) {
  const [name, setName] = useState("");
  const [tags, setTags] = useState("");

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Spec Library</h3>
        <button type="button" onClick={onRefresh} className="rounded-md border border-gray-300 p-1.5 text-gray-600">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>
      <div className="grid gap-2">
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="保存名称，空则使用 spec.name" className="rounded-md border border-gray-200 px-3 py-2 text-sm" />
        <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="tags, comma separated" className="rounded-md border border-gray-200 px-3 py-2 text-sm" />
        <button type="button" onClick={() => onSave(name || null, tags.split(",").map((tag) => tag.trim()).filter(Boolean))} className="inline-flex items-center justify-center gap-2 rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white">
          <Save className="h-4 w-4" />
          保存当前 Spec
        </button>
      </div>
      <div className="mt-4 max-h-64 space-y-2 overflow-auto">
        {specs.length === 0 && <div className="py-6 text-center text-sm text-gray-400">暂无保存的 Spec</div>}
        {specs.map((spec) => (
          <button key={spec.id} type="button" onClick={() => onLoad(spec.id)} className="w-full rounded-md border border-gray-100 px-3 py-2 text-left hover:bg-gray-50">
            <div className="text-sm font-medium text-gray-900">{spec.name}</div>
            <div className="mt-1 text-xs text-gray-500">{spec.market} · {spec.universe} · {spec.schema_version}</div>
          </button>
        ))}
      </div>
    </section>
  );
}
