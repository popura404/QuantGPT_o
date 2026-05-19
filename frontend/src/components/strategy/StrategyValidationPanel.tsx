import { CheckCircle2, XCircle } from "lucide-react";
import type { StrategyValidationResult } from "../../types/strategy";

export default function StrategyValidationPanel({ validation }: { validation: StrategyValidationResult | null }) {
  if (!validation) return null;
  return (
    <div className={`rounded-lg border p-4 ${validation.is_valid ? "border-green-200 bg-green-50" : "border-red-200 bg-red-50"}`}>
      <div className={`flex items-center gap-2 text-sm font-medium ${validation.is_valid ? "text-green-700" : "text-red-700"}`}>
        {validation.is_valid ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
        {validation.is_valid ? "校验通过" : "校验失败"}
      </div>
      {!validation.is_valid && (
        <ul className="mt-3 space-y-2 text-xs text-red-700">
          {validation.issues.map((issue, index) => (
            <li key={`${issue.code}-${index}`}>
              <span className="font-semibold">{issue.code}</span>
              {issue.path ? ` @ ${issue.path}` : ""}: {issue.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
