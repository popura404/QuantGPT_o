import type { DataQualityRequest } from "../../types/backtest";
import { useColorMode } from "../../contexts/ColorModeContext";

interface Props {
  value: DataQualityRequest;
  onChange: (value: DataQualityRequest) => void;
}

export default function DataQualitySettingsPanel({ value, onChange }: Props) {
  const { isDark } = useColorMode();
  const set = <K extends keyof DataQualityRequest>(key: K, next: DataQualityRequest[K]) => onChange({ ...value, [key]: next });
  const input = `mt-1 block w-full rounded-lg border px-3 py-2 text-sm ${
    isDark ? "border-gray-700 bg-gray-800 text-gray-100" : "border-gray-200 bg-white"
  }`;

  return (
    <section className={`rounded-lg border p-3 ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-100 bg-gray-50"}`}>
      <label className="flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={value.enabled ?? false} onChange={(event) => set("enabled", event.target.checked)} />
        数据质量门
      </label>
      <div className={`mt-3 grid gap-2 ${value.enabled ? "" : "opacity-70"}`}>
        <label className="block">
          <span className="text-xs text-gray-500">mode</span>
          <select value={value.mode ?? "filter"} onChange={(event) => set("mode", event.target.value as DataQualityRequest["mode"])} className={input}>
            <option value="report_only">report_only</option>
            <option value="filter">filter</option>
            <option value="strict">strict</option>
          </select>
        </label>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <NumberField label="min_price" value={value.min_price ?? 0.01} step={0.01} min={0.01} onChange={(n) => set("min_price", n)} />
          <NumberField label="max_abs_daily_ret" value={value.max_abs_daily_ret ?? 0.25} step={0.01} min={0.01} max={1} onChange={(n) => set("max_abs_daily_ret", n)} />
          <NumberField label="max_missing_ratio" value={value.max_missing_ratio_per_stock ?? 0.2} step={0.01} min={0} max={1} onChange={(n) => set("max_missing_ratio_per_stock", n)} />
          <NumberField label="drop_new_days" value={value.drop_new_listing_days ?? 60} min={0} onChange={(n) => set("drop_new_listing_days", n)} />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
          <CheckField label="positive volume" checked={value.require_positive_volume ?? true} onChange={(v) => set("require_positive_volume", v)} />
          <CheckField label="positive amount" checked={value.require_positive_amount ?? true} onChange={(v) => set("require_positive_amount", v)} />
          <CheckField label="drop ST" checked={value.drop_st ?? false} onChange={(v) => set("drop_st", v)} />
          <CheckField label="fail unknown adj" checked={value.fail_on_unknown_adjustment ?? false} onChange={(v) => set("fail_on_unknown_adjustment", v)} />
          <label className="block md:col-span-2">
            <span className="text-xs text-gray-500">adjustment</span>
            <select value={value.adjustment ?? "unknown"} onChange={(event) => set("adjustment", event.target.value as DataQualityRequest["adjustment"])} className={input}>
              <option value="unknown">unknown</option>
              <option value="qfq">qfq</option>
              <option value="hfq">hfq</option>
              <option value="none">none</option>
            </select>
          </label>
        </div>
      </div>
    </section>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
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
        onChange={(event) => onChange(Number(event.target.value))}
        className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm ${
          isDark ? "border-gray-700 bg-gray-800 text-gray-100" : "border-gray-200 bg-white"
        }`}
      />
    </label>
  );
}

function CheckField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 rounded-md px-2 py-1.5">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  );
}
