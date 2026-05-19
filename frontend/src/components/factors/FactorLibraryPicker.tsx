import { useEffect, useState } from "react";
import { Check, Loader2, Star } from "lucide-react";
import { fetchFactors, type SavedFactor } from "../../api/factorLibrary";

interface Props {
  open: boolean;
  title: string;
  existingExpressions: string[];
  onClose: () => void;
  onConfirm: (expressions: string[]) => void;
}

export default function FactorLibraryPicker({ open, title, existingExpressions, onClose, onConfirm }: Props) {
  const [factors, setFactors] = useState<SavedFactor[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const existing = new Set(existingExpressions.filter(Boolean));

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setSelected(new Set());
    fetchFactors()
      .then(setFactors)
      .catch(() => setFactors([]))
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  const toggle = (expression: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(expression)) next.delete(expression);
      else next.add(expression);
      return next;
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="mx-4 flex max-h-[70vh] w-full max-w-lg flex-col rounded-2xl bg-white shadow-xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
            <p className="mt-0.5 text-[11px] text-gray-400">已选 {selected.size} 个</p>
          </div>
          <button
            type="button"
            disabled={selected.size === 0}
            onClick={() => {
              onConfirm([...selected]);
              onClose();
            }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          >
            <Check className="h-3.5 w-3.5" />
            确认添加
          </button>
        </div>
        <div className="space-y-1.5 overflow-y-auto px-5 py-3">
          {loading && <div className="py-8 text-center text-xs text-gray-400"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />加载中...</div>}
          {!loading && factors.length === 0 && (
            <div className="py-8 text-center">
              <Star className="mx-auto mb-2 h-8 w-8 text-gray-200" />
              <p className="text-xs text-gray-500">因子库为空</p>
            </div>
          )}
          {!loading && factors.map((factor) => {
            const disabled = existing.has(factor.expression);
            const checked = selected.has(factor.expression) || disabled;
            return (
              <button
                key={factor.id}
                type="button"
                disabled={disabled}
                onClick={() => toggle(factor.expression)}
                className={`w-full rounded-lg border px-3 py-2.5 text-left transition-all ${
                  disabled
                    ? "cursor-not-allowed border-gray-100 bg-gray-50 opacity-50"
                    : selected.has(factor.expression)
                      ? "border-blue-300 bg-blue-50 ring-1 ring-blue-200"
                      : "border-gray-100 bg-white hover:border-blue-200 hover:shadow-sm"
                }`}
              >
                <div className="flex items-center gap-2">
                  <div className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${checked ? "border-blue-500 bg-blue-500" : "border-gray-300"}`}>
                    {checked && <Check className="h-3 w-3 text-white" />}
                  </div>
                  <code className="min-w-0 flex-1 truncate font-mono text-xs text-blue-700">{factor.expression}</code>
                  {disabled && <span className="text-[10px] text-gray-400">已添加</span>}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
