import { useCallback, useState } from "react";
import { BarChart3, Star } from "lucide-react";
import { compareFactors } from "../api/comparison";
import type { CompareFactorsResponse, CompareFactorResult } from "../api/comparison";
import { useColorMode } from "../contexts/ColorModeContext";
import CorrelationMatrix from "./CorrelationMatrix";
import ErrorNotice from "./common/ErrorNotice";
import LoadingButton from "./common/LoadingButton";
import FactorBacktestSettings, { type FactorBacktestSettingsValue } from "./factors/FactorBacktestSettings";
import FactorExpressionRows, { type FactorExpressionRow } from "./factors/FactorExpressionRows";
import FactorLibraryPicker from "./factors/FactorLibraryPicker";

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"];

const METRIC_LABELS: { key: string; label: string; format: (v: number) => string; higher_better: boolean }[] = [
  { key: "sharpe", label: "Top组Sharpe", format: (v) => v.toFixed(2), higher_better: true },
  { key: "ls_sharpe", label: "多空Sharpe", format: (v) => v.toFixed(2), higher_better: true },
  { key: "monotonicity", label: "单调性", format: (v) => v.toFixed(2), higher_better: true },
  { key: "ic_mean", label: "IC均值", format: (v) => v.toFixed(4), higher_better: true },
  { key: "rank_ic_mean", label: "Rank IC", format: (v) => v.toFixed(4), higher_better: true },
  { key: "ic_ir", label: "IC_IR", format: (v) => v.toFixed(2), higher_better: true },
  { key: "spread", label: "组间价差", format: (v) => (v * 100).toFixed(2) + "%", higher_better: true },
  { key: "turnover", label: "换手率", format: (v) => (v * 100).toFixed(1) + "%", higher_better: false },
  { key: "wq_fitness", label: "WQ Fitness", format: (v) => v.toFixed(2), higher_better: true },
];

interface Props {
  savedExpressions?: string[];
}

export default function FactorComparison({ savedExpressions }: Props) {
  const [factors, setFactors] = useState<FactorExpressionRow[]>([
    { expression: "", label: "" },
    { expression: "", label: "" },
  ]);
  const [result, setResult] = useState<CompareFactorsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [settings, setSettings] = useState<FactorBacktestSettingsValue>({
    universe: "hs300",
    start_date: "2023-01-01",
    end_date: "2025-12-31",
    n_groups: 5,
    holding_period: 5,
  });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCompare = useCallback(async () => {
    const valid = factors.filter((factor) => factor.expression.trim());
    if (valid.length < 2) {
      setError("至少需要2个因子表达式");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await compareFactors(
        valid.map((factor) => ({ expression: factor.expression, label: factor.label || undefined })),
        {
          universe: settings.universe,
          start_date: settings.start_date,
          end_date: settings.end_date,
          n_groups: settings.n_groups,
          holding_period: settings.holding_period,
        },
      );
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "对比失败");
    } finally {
      setLoading(false);
    }
  }, [factors, settings]);

  const appendExpressions = (expressions: string[]) => {
    setFactors((prev) => {
      const existing = new Set(prev.map((factor) => factor.expression).filter(Boolean));
      const additions = expressions.filter((expression) => !existing.has(expression)).map((expression) => ({ expression, label: "" }));
      const next = [...prev];
      let cursor = 0;
      for (let index = 0; index < next.length && cursor < additions.length; index += 1) {
        if (!next[index].expression.trim()) next[index] = additions[cursor++];
      }
      while (cursor < additions.length && next.length < 6) next.push(additions[cursor++]);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <ErrorNotice message={error} onClear={() => setError(null)} />
      <FactorExpressionRows
        rows={factors}
        onChange={setFactors}
        mode="comparison"
        minRows={2}
        maxRows={6}
        savedExpressions={savedExpressions}
      />
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => setPickerOpen(true)} className="inline-flex items-center gap-1 text-xs text-amber-600 hover:text-amber-700">
          <Star className="h-3 w-3" />
          从因子库选择
        </button>
        <LoadingButton
          type="button"
          onClick={() => void handleCompare()}
          loading={loading}
          disabled={factors.filter((factor) => factor.expression.trim()).length < 2}
          icon={<BarChart3 className="h-3.5 w-3.5" />}
          className="ml-auto bg-blue-600 hover:bg-blue-700"
        >
          {loading ? "对比中..." : "开始对比"}
        </LoadingButton>
      </div>
      <FactorLibraryPicker
        open={pickerOpen}
        title="从因子库选择"
        existingExpressions={factors.map((factor) => factor.expression)}
        onClose={() => setPickerOpen(false)}
        onConfirm={appendExpressions}
      />
      <FactorBacktestSettings value={settings} onChange={setSettings} supports={{ benchmark: false }} />
      {result && <ComparisonResults data={result} />}
    </div>
  );
}

function ComparisonResults({ data }: { data: CompareFactorsResponse }) {
  const { positiveClass, negativeClass, isDark } = useColorMode();
  const successFactors = data.factors.filter((factor): factor is CompareFactorResult & { metrics: NonNullable<CompareFactorResult["metrics"]> } =>
    factor.status === "success" && !!factor.metrics
  );
  const failedFactors = data.factors.filter((factor) => factor.status === "failed");

  return (
    <div className="space-y-4">
      {failedFactors.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3">
          <div className="text-sm font-medium text-red-700">失败因子</div>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-red-700">
            {failedFactors.map((factor, index) => (
              <li key={`${factor.expression}-${index}`}>
                <code>{factor.label || factor.expression}</code>: {factor.error ?? "unknown"}
              </li>
            ))}
          </ul>
        </div>
      )}
      {successFactors.length === 0 ? (
        <div className="py-4 text-center text-xs text-gray-400">所有因子回测均失败</div>
      ) : (
        <>
          <div className={`overflow-x-auto rounded-lg border ${isDark ? "border-gray-700" : "border-gray-200"}`}>
            <table className="w-full text-xs">
              <thead>
                <tr className={`${isDark ? "bg-gray-800 border-gray-700" : "bg-gray-50 border-gray-200"} border-b`}>
                  <th className={`px-3 py-2 text-left font-medium ${isDark ? "text-gray-400" : "text-gray-500"}`}>指标</th>
                  {successFactors.map((factor, index) => (
                    <th key={index} className="px-3 py-2 text-right font-medium" style={{ color: COLORS[index % COLORS.length] }}>
                      {factor.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className={`divide-y ${isDark ? "divide-gray-700" : "divide-gray-100"}`}>
                {METRIC_LABELS.map(({ key, label, format, higher_better }) => {
                  const values = successFactors.map((factor) => factor.metrics[key as keyof typeof factor.metrics] ?? 0);
                  const best = higher_better ? Math.max(...values) : Math.min(...values);
                  return (
                    <tr key={key}>
                      <td className={`px-3 py-2 ${isDark ? "text-gray-400" : "text-gray-600"}`}>{label}</td>
                      {successFactors.map((factor, index) => {
                        const value = factor.metrics[key as keyof typeof factor.metrics] ?? 0;
                        const isBest = value === best && successFactors.length > 1;
                        return (
                          <td key={index} className={`px-3 py-2 text-right font-mono ${isBest ? `font-bold ${positiveClass}` : `${isDark ? "text-gray-300" : "text-gray-700"}`}`}>
                            {format(value)}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className={`rounded-lg border p-4 ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-white"}`}>
            <h4 className={`mb-3 text-xs font-medium ${isDark ? "text-gray-400" : "text-gray-600"}`}>Top组累计收益对比</h4>
            <div className="space-y-2">
              {successFactors.map((factor, index) => {
                const returns = factor.cumulative_returns ?? [];
                const finalValue = returns.length > 0 ? returns[returns.length - 1].value : 1;
                const totalReturn = ((finalValue - 1) * 100).toFixed(1);
                const maxValue = returns.length > 0 ? Math.max(...returns.map((row) => row.value)) : 1;
                const barWidth = finalValue > 0 ? Math.min(100, (finalValue / maxValue) * 80) : 0;
                return (
                  <div key={index} className="flex items-center gap-3">
                    <span className="w-24 truncate text-xs" style={{ color: COLORS[index % COLORS.length] }} title={factor.label}>{factor.label}</span>
                    <div className={`relative h-4 flex-1 overflow-hidden rounded-full ${isDark ? "bg-gray-800" : "bg-gray-100"}`}>
                      <div className="h-full rounded-full transition-all duration-500" style={{ width: `${barWidth}%`, backgroundColor: COLORS[index % COLORS.length], opacity: 0.7 }} />
                    </div>
                    <span className={`w-16 text-right font-mono text-xs ${Number(totalReturn) >= 0 ? positiveClass : negativeClass}`}>
                      {Number(totalReturn) >= 0 ? "+" : ""}{totalReturn}%
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {data.correlation && (
            <div className={`rounded-lg border p-4 ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-white"}`}>
              <h4 className={`mb-3 text-xs font-medium ${isDark ? "text-gray-400" : "text-gray-600"}`}>因子相关性矩阵</h4>
              <CorrelationMatrix labels={data.correlation.labels} matrix={data.correlation.matrix} />
              <p className="mt-2 text-[10px] text-gray-400">高相关（&gt;0.5）因子提供相似信息，组合时应降低权重</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
