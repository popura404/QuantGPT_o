import type { StrategyTemplateSummary } from "../../types/strategy";

interface Props {
  templates: StrategyTemplateSummary[];
  selectedId: string;
  onSelect: (templateId: string) => void;
}

export default function StrategyTemplatePicker({ templates, selectedId, onSelect }: Props) {
  const selected = templates.find((template) => template.id === selectedId);
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">模板</h3>
          <p className="mt-1 text-xs text-gray-500">{selected?.description ?? "选择 StrategySpec 模板"}</p>
        </div>
        <select value={selectedId} onChange={(event) => onSelect(event.target.value)} className="h-9 rounded-md border border-gray-300 px-3 text-sm">
          {templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
        </select>
      </div>
      {selected && (
        <div className="mt-3 grid gap-2 md:grid-cols-[160px_minmax(0,1fr)]">
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            risk: {selected.risk_label}
          </div>
          <pre className="max-h-32 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-gray-700">
            {JSON.stringify(selected.parameter_bounds, null, 2)}
          </pre>
        </div>
      )}
    </section>
  );
}
