import type { BacktestResult } from "../../types/backtest";
import { useColorMode } from "../../contexts/ColorModeContext";

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function asArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

export default function DataQualitySummaryCard({ result }: { result: BacktestResult }) {
  const { isDark } = useColorMode();
  const dataQuality = isObject(result.data_quality)
    ? result.data_quality
    : isObject(result.oos_result?.data_quality)
      ? result.oos_result.data_quality
      : null;
  if (!dataQuality) return null;

  const warnings = asArray(dataQuality.warnings);
  const issues = asArray(dataQuality.issues);

  return (
    <section className={`rounded-xl border p-4 ${isDark ? "border-cyan-800 bg-cyan-950/20" : "border-cyan-200 bg-cyan-50"}`}>
      <div className={`text-sm font-medium ${isDark ? "text-cyan-200" : "text-cyan-900"}`}>数据质量</div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        {([
          ["dropped_rows", dataQuality.dropped_rows],
          ["dropped_stocks", dataQuality.dropped_stocks],
          ["adjustment", dataQuality.adjustment],
          ["scope", dataQuality.data_quality_scope ?? dataQuality.scope],
        ] as Array<[string, unknown]>).map(([label, value]) => (
          <div key={String(label)} className={`rounded-md p-3 ${isDark ? "bg-gray-900" : "bg-white"}`}>
            <div className={isDark ? "text-gray-400" : "text-gray-500"}>{label}</div>
            <div className={`mt-1 font-mono ${isDark ? "text-gray-100" : "text-gray-900"}`}>{String(value ?? "-")}</div>
          </div>
        ))}
      </div>
      {(warnings.length > 0 || issues.length > 0) && (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {warnings.length > 0 && <MessageList title="warnings" items={warnings} tone="amber" />}
          {issues.length > 0 && <MessageList title="issues" items={issues} tone="red" />}
        </div>
      )}
    </section>
  );
}

function MessageList({ title, items, tone }: { title: string; items: string[]; tone: "amber" | "red" }) {
  const color = tone === "amber" ? "text-amber-700 bg-amber-50 border-amber-200" : "text-red-700 bg-red-50 border-red-200";
  return (
    <div className={`rounded-md border p-3 text-xs ${color}`}>
      <div className="font-medium">{title}</div>
      <ul className="mt-2 list-disc space-y-1 pl-4">
        {items.map((item, index) => <li key={index}>{item}</li>)}
      </ul>
    </div>
  );
}
