import { useState } from "react";
import { Activity, SlidersHorizontal } from "lucide-react";
import {
  diagnoseStrategy,
  optimizeStrategyCandidate,
  runStrategyAntiOverfit,
  runStrategyRollingValidation,
} from "../../api/strategy";
import type { StrategyBacktestResultPayload } from "../../types/strategy";

interface Props {
  result: StrategyBacktestResultPayload | null;
  spec: Record<string, unknown> | null;
}

export default function StrategyDiagnosticsPanel({ result, spec }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [output, setOutput] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(label: string, fn: () => Promise<Record<string, unknown>>) {
    if (!result) return;
    setBusy(label);
    setError(null);
    try {
      setOutput(await fn());
    } catch (err) {
      setError(err instanceof Error ? err.message : `${label} failed`);
    } finally {
      setBusy(null);
    }
  }

  const signals = (Array.isArray(result?.target_weights) ? result?.target_weights : result?.latest_holdings ?? []) as Record<string, unknown>[];

  return (
    <section className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-gray-900">诊断与收敛</h3>
      <div className="flex flex-wrap gap-2">
        <Action label="diagnose" busy={busy} onClick={() => run("diagnose", () => diagnoseStrategy(result as StrategyBacktestResultPayload))} />
        <Action label="anti-overfit" busy={busy} onClick={() => run("anti-overfit", () => runStrategyAntiOverfit(result as StrategyBacktestResultPayload))} />
        <Action label="rolling validation" busy={busy} onClick={() => run("rolling validation", () => runStrategyRollingValidation(result as StrategyBacktestResultPayload, 3))} />
        <button
          type="button"
          disabled={!result || busy !== null}
          onClick={() => run("optimize", () => optimizeStrategyCandidate(signals, spec ?? {}))}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium disabled:opacity-50"
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
          optimize
        </button>
      </div>
      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {output && <pre className="max-h-72 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-gray-700">{JSON.stringify(output, null, 2)}</pre>}
    </section>
  );
}

function Action({ label, busy, onClick }: { label: string; busy: string | null; onClick: () => void }) {
  return (
    <button type="button" disabled={busy !== null} onClick={onClick} className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-1.5 text-xs font-medium disabled:opacity-50">
      <Activity className={`h-3.5 w-3.5 ${busy === label ? "animate-pulse" : ""}`} />
      {label}
    </button>
  );
}
