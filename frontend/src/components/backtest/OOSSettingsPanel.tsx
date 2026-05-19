import type { OOSRequest } from "../../types/backtest";
import { useColorMode } from "../../contexts/ColorModeContext";

interface Props {
  enabled: boolean;
  value: OOSRequest;
  onEnabledChange: (enabled: boolean) => void;
  onChange: (value: OOSRequest) => void;
}

export default function OOSSettingsPanel({ enabled, value, onEnabledChange, onChange }: Props) {
  const { isDark } = useColorMode();
  const set = <K extends keyof OOSRequest>(key: K, next: OOSRequest[K]) => onChange({ ...value, [key]: next });
  const input = `mt-1 block w-full rounded-lg border px-3 py-2 text-sm ${
    isDark ? "border-gray-700 bg-gray-800 text-gray-100" : "border-gray-200 bg-white"
  }`;

  return (
    <section className={`rounded-lg border p-3 ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-100 bg-gray-50"}`}>
      <label className="flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={enabled} onChange={(event) => onEnabledChange(event.target.checked)} />
        样本外验证
      </label>
      {enabled && (
        <div className="mt-3 space-y-3">
          <label className="block">
            <span className="text-xs text-gray-500">切分方法</span>
            <select value={value.method ?? "date_ratio"} onChange={(event) => set("method", event.target.value as OOSRequest["method"])} className={input}>
              <option value="date_ratio">date_ratio</option>
              <option value="date_cut">date_cut</option>
            </select>
          </label>
          {(value.method ?? "date_ratio") === "date_ratio" ? (
            <div className="grid grid-cols-3 gap-2">
              <NumberField label="train_ratio" value={value.train_ratio ?? 0.6} step={0.05} min={0} max={1} onChange={(n) => set("train_ratio", n ?? 0.6)} />
              <NumberField label="valid_ratio" value={value.valid_ratio ?? 0.2} step={0.05} min={0} max={1} onChange={(n) => set("valid_ratio", n ?? 0.2)} />
              <NumberField label="test_ratio" value={value.test_ratio ?? 0.2} step={0.05} min={0} max={1} onChange={(n) => set("test_ratio", n ?? 0.2)} />
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <DateField label="train_end" value={value.train_end ?? ""} onChange={(v) => set("train_end", v || null)} />
              <DateField label="valid_end" value={value.valid_end ?? ""} onChange={(v) => set("valid_end", v || null)} />
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <NumberField label="min_train_days" value={value.min_train_days ?? 252} min={1} onChange={(n) => set("min_train_days", n ?? 252)} />
            <NumberField label="min_valid_days" value={value.min_valid_days ?? 126} min={1} onChange={(n) => set("min_valid_days", n ?? 126)} />
            <NumberField label="min_test_days" value={value.min_test_days ?? 126} min={1} onChange={(n) => set("min_test_days", n ?? 126)} />
            <NumberField label="warmup_days" value={value.warmup_days ?? ""} min={0} optional onChange={(n) => set("warmup_days", n)} />
          </div>
        </div>
      )}
    </section>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  step,
  optional,
  onChange,
}: {
  label: string;
  value: number | "";
  min?: number;
  max?: number;
  step?: number;
  optional?: boolean;
  onChange: (value: number | null) => void;
}) {
  const { isDark } = useColorMode();
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step ?? 1}
        onChange={(event) => onChange(optional && event.target.value === "" ? null : Number(event.target.value))}
        className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm ${
          isDark ? "border-gray-700 bg-gray-800 text-gray-100" : "border-gray-200 bg-white"
        }`}
      />
    </label>
  );
}

function DateField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const { isDark } = useColorMode();
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <input
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm ${
          isDark ? "border-gray-700 bg-gray-800 text-gray-100" : "border-gray-200 bg-white"
        }`}
      />
    </label>
  );
}
