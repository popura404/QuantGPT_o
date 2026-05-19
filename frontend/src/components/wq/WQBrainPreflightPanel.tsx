import type { SubmissionPreflight } from "../../types/backtest";

interface Props {
  preflight?: SubmissionPreflight | null;
}

export default function WQBrainPreflightPanel({ preflight }: Props) {
  if (!preflight) return null;
  const allowed = Boolean(preflight.allowed);
  const reasons = Array.isArray(preflight.reasons) ? preflight.reasons : [];
  const warnings = Array.isArray(preflight.warnings) ? preflight.warnings : [];

  return (
    <div className={`rounded-lg border p-3 text-sm ${allowed ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-800"}`}>
      <div className="font-medium">Preflight: {allowed ? "allowed" : "blocked"}</div>
      {preflight.override_reason && <div className="mt-1 text-xs">override: {preflight.override_reason}</div>}
      {reasons.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
          {reasons.map((reason, index) => <li key={`reason-${index}`}>{reason}</li>)}
        </ul>
      )}
      {warnings.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-700">
          {warnings.map((warning, index) => <li key={`warning-${index}`}>{warning}</li>)}
        </ul>
      )}
    </div>
  );
}
