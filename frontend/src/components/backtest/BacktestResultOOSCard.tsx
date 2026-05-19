import type { BacktestResult } from "../../types/backtest";
import { useColorMode } from "../../contexts/ColorModeContext";

function formatMetric(value: unknown): string {
  if (typeof value === "number") return Number.isFinite(value) ? value.toFixed(4) : "-";
  if (value === null || value === undefined) return "-";
  return String(value);
}

export default function BacktestResultOOSCard({ result }: { result: BacktestResult }) {
  const { isDark } = useColorMode();
  const oos = result.oos_result;
  if (!oos) return null;

  return (
    <section className={`rounded-xl border p-4 ${isDark ? "border-emerald-800 bg-emerald-950/30" : "border-emerald-200 bg-emerald-50"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className={`text-sm font-medium ${isDark ? "text-emerald-200" : "text-emerald-900"}`}>样本外验证</div>
          <div className={`mt-1 text-xs ${isDark ? "text-emerald-300" : "text-emerald-700"}`}>
            {oos.direction_policy ?? result.direction_policy ?? "train_fixed"} · fixed_direction={oos.fixed_direction ?? "-"} · risk={oos.oos_risk ?? result.scoring?.overfit_risk ?? "-"}
          </div>
        </div>
        {result.scoring?.decision && (
          <div className={`rounded-md px-2 py-1 text-xs font-medium ${isDark ? "bg-gray-900 text-emerald-200" : "bg-white text-emerald-800"}`}>
            {result.scoring.decision} · {result.scoring.score?.toFixed(1) ?? "-"}
          </div>
        )}
      </div>
      <div className="mt-3 grid gap-2 text-xs md:grid-cols-3">
        {(["train", "valid", "test"] as const).map((key) => {
          const block = oos[key];
          const metrics = block?.metrics ?? {};
          return (
            <div key={key} className={`rounded-md p-3 ${isDark ? "bg-gray-900" : "bg-white"}`}>
              <div className={`font-medium ${isDark ? "text-gray-200" : "text-gray-900"}`}>{key}</div>
              <div className={`mt-1 ${isDark ? "text-gray-400" : "text-gray-500"}`}>{block?.period?.join(" ~ ") ?? "-"}</div>
              <div className="mt-2 grid grid-cols-2 gap-1 font-mono">
                <span>Sharpe {formatMetric(metrics.long_short_sharpe)}</span>
                <span>IC {formatMetric(metrics.direction_adjusted_rank_ic_mean ?? metrics.rank_ic_mean ?? metrics.ic_mean)}</span>
                <span>Turn {formatMetric(metrics.turnover)}</span>
                <span>DD {formatMetric(metrics.max_drawdown)}</span>
              </div>
            </div>
          );
        })}
      </div>
      {oos.warnings && oos.warnings.length > 0 && (
        <ul className={`mt-3 list-disc space-y-1 pl-5 text-xs ${isDark ? "text-amber-200" : "text-amber-800"}`}>
          {oos.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
        </ul>
      )}
    </section>
  );
}
