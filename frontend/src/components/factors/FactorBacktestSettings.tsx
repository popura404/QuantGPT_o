export interface FactorBacktestSettingsValue {
  universe: string;
  start_date: string;
  end_date: string;
  n_groups?: number;
  holding_period?: number;
  benchmark?: string;
}

interface Props {
  value: FactorBacktestSettingsValue;
  onChange: (value: FactorBacktestSettingsValue) => void;
  supports?: {
    n_groups?: boolean;
    holding_period?: boolean;
    benchmark?: boolean;
  };
}

export default function FactorBacktestSettings({ value, onChange, supports }: Props) {
  const enabled = {
    n_groups: supports?.n_groups ?? true,
    holding_period: supports?.holding_period ?? true,
    benchmark: supports?.benchmark ?? true,
  };
  const set = <K extends keyof FactorBacktestSettingsValue>(key: K, next: FactorBacktestSettingsValue[K]) => {
    onChange({ ...value, [key]: next });
  };
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
      <Select label="universe" value={value.universe} options={["small_scale", "hs300", "csi500", "csi1000", "csi2000"]} onChange={(v) => set("universe", v)} />
      <DateInput label="start_date" value={value.start_date} onChange={(v) => set("start_date", v)} />
      <DateInput label="end_date" value={value.end_date} onChange={(v) => set("end_date", v)} />
      {enabled.n_groups && <NumberInput label="n_groups" value={value.n_groups ?? 5} min={2} max={20} onChange={(v) => set("n_groups", v)} />}
      {enabled.holding_period && <NumberInput label="holding_period" value={value.holding_period ?? 5} min={1} max={60} onChange={(v) => set("holding_period", v)} />}
      {enabled.benchmark && <Select label="benchmark" value={value.benchmark ?? "hs300"} options={["hs300", "zz500", "csi1000", "sz50"]} onChange={(v) => set("benchmark", v)} />}
    </div>
  );
}

function Select({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs">
        {options.map((option) => <option key={option} value={option}>{option}</option>)}
      </select>
    </label>
  );
}

function DateInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <input type="date" value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs" />
    </label>
  );
}

function NumberInput({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <input type="number" value={value} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-xs" />
    </label>
  );
}
