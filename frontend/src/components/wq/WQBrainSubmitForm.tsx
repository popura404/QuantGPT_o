import { useState } from "react";
import { Loader2, Send } from "lucide-react";
import type { WQBrainSimulationPayload } from "../../types/wqBrain";

interface Props {
  loading: boolean;
  onSubmit: (payload: WQBrainSimulationPayload) => void;
}

export default function WQBrainSubmitForm({ loading, onSubmit }: Props) {
  const [payload, setPayload] = useState<WQBrainSimulationPayload>({
    expression: "",
    tag: "frontend-research",
    region: "USA",
    universe: "TOP3000",
    delay: 1,
    decay: 0,
    neutralization: "SUBINDUSTRY",
    truncation: 0.08,
    account: "primary",
    auto_submit: false,
    submission_override_reason: "",
  });
  const autoSubmitDisabled = payload.account !== "primary";

  const set = <K extends keyof WQBrainSimulationPayload>(key: K, value: WQBrainSimulationPayload[K]) => {
    setPayload((prev) => ({
      ...prev,
      [key]: value,
      auto_submit: key === "account" && value !== "primary" ? false : prev.auto_submit,
    }));
  };

  return (
    <form
      className="space-y-3 rounded-xl border border-gray-200 bg-white p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!payload.expression.trim() || !payload.tag.trim()) return;
        onSubmit({ ...payload, expression: payload.expression.trim(), tag: payload.tag.trim() });
      }}
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">模拟提交</h3>
        <button type="submit" disabled={loading || !payload.expression.trim()} className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          运行模拟
        </button>
      </div>
      <textarea
        value={payload.expression}
        onChange={(event) => set("expression", event.target.value)}
        rows={4}
        placeholder="FASTEXPR expression"
        className="w-full rounded-lg border border-gray-200 px-3 py-2 font-mono text-xs"
      />
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <TextField label="tag" value={payload.tag} onChange={(v) => set("tag", v)} />
        <SelectField label="account" value={payload.account} options={["primary", "alt"]} onChange={(v) => set("account", v as WQBrainSimulationPayload["account"])} />
        <SelectField label="region" value={payload.region} options={["USA"]} onChange={(v) => set("region", v)} />
        <SelectField label="universe" value={payload.universe} options={["TOP3000", "TOP1000", "TOP500", "TOP200"]} onChange={(v) => set("universe", v)} />
        <NumberField label="delay" value={payload.delay} min={0} max={1} onChange={(v) => set("delay", v)} />
        <NumberField label="decay" value={payload.decay} min={0} max={20} onChange={(v) => set("decay", v)} />
        <SelectField label="neutralization" value={payload.neutralization} options={["SUBINDUSTRY", "INDUSTRY", "SECTOR", "MARKET", "NONE"]} onChange={(v) => set("neutralization", v)} />
        <NumberField label="truncation" value={payload.truncation} min={0} max={0.5} step={0.01} onChange={(v) => set("truncation", v)} />
      </div>
      <label className="flex items-center gap-2 text-sm text-gray-700">
        <input
          type="checkbox"
          checked={payload.auto_submit}
          disabled={autoSubmitDisabled}
          onChange={(event) => set("auto_submit", event.target.checked)}
        />
        auto_submit
        {autoSubmitDisabled && <span className="text-xs text-red-600">alt 账号禁止自动正式提交</span>}
      </label>
      <textarea
        value={payload.submission_override_reason ?? ""}
        onChange={(event) => set("submission_override_reason", event.target.value)}
        rows={2}
        placeholder="submission_override_reason"
        className="w-full rounded-lg border border-gray-200 px-3 py-2 text-xs"
      />
    </form>
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

function NumberField({ label, value, min, max, step, onChange }: { label: string; value: number; min?: number; max?: number; step?: number; onChange: (value: number) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <input type="number" value={value} min={min} max={max} step={step ?? 1} onChange={(event) => onChange(Number(event.target.value))} className="mt-1 w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm" />
    </label>
  );
}

function SelectField({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-gray-500">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm">
        {options.map((option) => <option key={option} value={option}>{option}</option>)}
      </select>
    </label>
  );
}
