import { Plus, Trash2 } from "lucide-react";

function cloneSpec(spec: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(spec)) as Record<string, unknown>;
}

function getObject(parent: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = parent[key];
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  parent[key] = {};
  return parent[key] as Record<string, unknown>;
}

interface Props {
  spec: Record<string, unknown> | null;
  onChange: (spec: Record<string, unknown>) => void;
}

export default function StrategyParameterForm({ spec, onChange }: Props) {
  if (!spec) {
    return <section className="rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-500">JSON 无法解析</section>;
  }
  const factors = Array.isArray(spec.factors) ? spec.factors as Record<string, unknown>[] : [];
  const signalRules = (spec.signal_rules ?? {}) as Record<string, unknown>;
  const portfolioRule = (spec.portfolio_rule ?? {}) as Record<string, unknown>;
  const riskRules = (spec.risk_rules ?? {}) as Record<string, unknown>;

  const patch = (mutator: (draft: Record<string, unknown>) => void) => {
    const draft = cloneSpec(spec);
    mutator(draft);
    onChange(draft);
  };

  return (
    <section className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-gray-900">结构化参数</h3>
      <div className="grid grid-cols-2 gap-2">
        <TextField label="name" value={String(spec.name ?? "")} onChange={(v) => patch((d) => { d.name = v; })} />
        <TextField label="universe" value={String(spec.universe ?? "")} onChange={(v) => patch((d) => { d.universe = v; })} />
        <NumberField label="top_n" value={Number(signalRules.top_n ?? 20)} onChange={(v) => patch((d) => { getObject(d, "signal_rules").top_n = v; })} />
        <NumberField label="long_quantile" value={Number(signalRules.long_quantile ?? 0.2)} step={0.01} onChange={(v) => patch((d) => { getObject(d, "signal_rules").long_quantile = v; })} />
        <NumberField label="rebalance_period" value={Number(portfolioRule.rebalance_period ?? 5)} onChange={(v) => patch((d) => { getObject(d, "portfolio_rule").rebalance_period = v; })} />
        <NumberField label="max_asset_weight" value={Number(riskRules.max_asset_weight ?? 0.05)} step={0.01} onChange={(v) => patch((d) => { getObject(d, "risk_rules").max_asset_weight = v; })} />
      </div>
      <label className="block">
        <span className="text-xs text-gray-500">weighting</span>
        <select value={String(portfolioRule.weighting ?? "equal_weight")} onChange={(event) => patch((d) => { getObject(d, "portfolio_rule").weighting = event.target.value; })} className="mt-1 w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm">
          <option value="equal_weight">equal_weight</option>
          <option value="score_weighted">score_weighted</option>
        </select>
      </label>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-xs font-medium text-gray-500">factors</div>
          <button type="button" onClick={() => patch((d) => {
            const list = Array.isArray(d.factors) ? [...d.factors] : [];
            list.push({ id: `factor_${list.length + 1}`, expression: "", direction: "higher_is_better", weight: 1 });
            d.factors = list;
          })} className="inline-flex items-center gap-1 text-xs text-blue-700">
            <Plus className="h-3 w-3" />
            添加
          </button>
        </div>
        {factors.map((factor, index) => (
          <div key={index} className="rounded-md border border-gray-100 p-2">
            <div className="mb-2 flex justify-end">
              <button type="button" onClick={() => patch((d) => { d.factors = factors.filter((_, i) => i !== index); })} className="text-gray-400 hover:text-red-600">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="grid grid-cols-[1fr_120px_90px] gap-2">
              <TextField label="expression" value={String(factor.expression ?? "")} onChange={(v) => patch((d) => {
                const list = [...factors];
                list[index] = { ...list[index], expression: v };
                d.factors = list;
              })} />
              <select value={String(factor.direction ?? "higher_is_better")} onChange={(event) => patch((d) => {
                const list = [...factors];
                list[index] = { ...list[index], direction: event.target.value };
                d.factors = list;
              })} className="mt-5 rounded-md border border-gray-200 px-2 text-xs">
                <option value="higher_is_better">higher</option>
                <option value="lower_is_better">lower</option>
              </select>
              <NumberField label="weight" value={Number(factor.weight ?? 1)} step={0.1} onChange={(v) => patch((d) => {
                const list = [...factors];
                list[index] = { ...list[index], weight: v };
                d.factors = list;
              })} />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm" />
    </label>
  );
}

function NumberField({ label, value, step, onChange }: { label: string; value: number; step?: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <input type="number" value={Number.isFinite(value) ? value : 0} step={step ?? 1} onChange={(event) => onChange(Number(event.target.value))} className="mt-1 w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm" />
    </label>
  );
}
