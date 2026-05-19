import { Plus, Trash2 } from "lucide-react";

export interface FactorExpressionRow {
  expression: string;
  label?: string;
  weight?: number;
}

interface Props {
  rows: FactorExpressionRow[];
  onChange: (rows: FactorExpressionRow[]) => void;
  mode: "weighted" | "comparison";
  minRows: number;
  maxRows: number;
  savedExpressions?: string[];
}

export default function FactorExpressionRows({ rows, onChange, mode, minRows, maxRows, savedExpressions }: Props) {
  const update = (index: number, patch: Partial<FactorExpressionRow>) => {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };
  const addRow = () => {
    if (rows.length >= maxRows) return;
    onChange([...rows, { expression: "", label: "", weight: 1 }]);
  };
  const removeRow = (index: number) => {
    if (rows.length <= minRows) return;
    onChange(rows.filter((_, i) => i !== index));
  };
  const totalWeight = rows.reduce((sum, row) => sum + (row.weight ?? 0), 0);

  return (
    <div className="space-y-2">
      {rows.map((row, index) => (
        <div key={index} className="flex items-center gap-2">
          <span className="w-6 shrink-0 text-right text-xs text-gray-400">{index + 1}.</span>
          {mode === "comparison" && (
            <input
              value={row.label ?? ""}
              onChange={(event) => update(index, { label: event.target.value })}
              placeholder={`因子${index + 1}`}
              className="w-24 rounded-lg border border-gray-200 px-2 py-1.5 text-xs"
            />
          )}
          <input
            value={row.expression}
            onChange={(event) => update(index, { expression: event.target.value })}
            placeholder="因子表达式"
            className="min-w-0 flex-1 rounded-lg border border-gray-200 px-3 py-1.5 font-mono text-xs"
            list={savedExpressions ? `factor-row-${mode}-${index}` : undefined}
          />
          {savedExpressions && (
            <datalist id={`factor-row-${mode}-${index}`}>
              {savedExpressions.map((expression) => <option key={expression} value={expression} />)}
            </datalist>
          )}
          {mode === "weighted" && (
            <>
              <input
                type="number"
                min={0}
                max={10}
                step={0.1}
                value={row.weight ?? 1}
                onChange={(event) => update(index, { weight: Number(event.target.value) })}
                className="w-16 rounded-lg border border-gray-200 px-2 py-1.5 text-center text-xs"
              />
              <span className="w-10 text-[10px] text-gray-400">{totalWeight > 0 ? `${(((row.weight ?? 0) / totalWeight) * 100).toFixed(0)}%` : "-"}</span>
            </>
          )}
          <button type="button" onClick={() => removeRow(index)} disabled={rows.length <= minRows} className="p-1 text-gray-400 hover:text-red-600 disabled:opacity-30">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <button type="button" onClick={addRow} disabled={rows.length >= maxRows} className="inline-flex items-center gap-1 text-xs text-blue-700 disabled:opacity-50">
        <Plus className="h-3.5 w-3.5" />
        添加因子 ({rows.length}/{maxRows})
      </button>
    </div>
  );
}
