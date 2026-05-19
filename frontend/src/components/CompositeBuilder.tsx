import { useCallback, useState } from "react";
import { Play, Shuffle, Star } from "lucide-react";
import type { CompositeBacktestPayload, FactorItem } from "../api/composite";
import AttributionChart from "./AttributionChart";
import ErrorNotice from "./common/ErrorNotice";
import LoadingButton from "./common/LoadingButton";
import FactorBacktestSettings, { type FactorBacktestSettingsValue } from "./factors/FactorBacktestSettings";
import FactorExpressionRows, { type FactorExpressionRow } from "./factors/FactorExpressionRows";
import FactorLibraryPicker from "./factors/FactorLibraryPicker";

interface Props {
  onSubmit: (payload: CompositeBacktestPayload) => Promise<void>;
  isLoading: boolean;
  savedExpressions?: string[];
}

const METHODS = [
  { value: "weighted_rank", label: "加权排名", desc: "各因子截面排名后加权求和（推荐）" },
  { value: "weighted_zscore", label: "加权Z-Score", desc: "各因子标准化后加权求和" },
  { value: "equal_weight", label: "等权", desc: "忽略权重，各因子等权组合" },
];

export default function CompositeBuilder({ onSubmit, isLoading, savedExpressions }: Props) {
  const [factors, setFactors] = useState<FactorExpressionRow[]>([
    { expression: "", weight: 1 },
    { expression: "", weight: 1 },
  ]);
  const [method, setMethod] = useState("weighted_rank");
  const [settings, setSettings] = useState<FactorBacktestSettingsValue>({
    universe: "hs300",
    start_date: "2023-01-01",
    end_date: "2025-12-31",
    n_groups: 5,
    holding_period: 5,
    benchmark: "hs300",
  });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(async () => {
    const validFactors: FactorItem[] = factors
      .filter((factor) => factor.expression.trim())
      .map((factor) => ({ expression: factor.expression.trim(), weight: factor.weight ?? 1, label: factor.label }));
    if (validFactors.length < 2) {
      setError("至少需要2个有效因子表达式");
      return;
    }
    setError(null);
    try {
      await onSubmit({
        factors: validFactors,
        combination_method: method,
        universe: settings.universe,
        start_date: settings.start_date,
        end_date: settings.end_date,
        n_groups: settings.n_groups ?? 5,
        holding_period: settings.holding_period ?? 5,
        benchmark: settings.benchmark ?? "hs300",
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "组合回测失败");
    }
  }, [factors, method, onSubmit, settings]);

  const appendExpressions = (expressions: string[]) => {
    setFactors((prev) => {
      const existing = new Set(prev.map((factor) => factor.expression).filter(Boolean));
      const additions = expressions.filter((expression) => !existing.has(expression)).map((expression) => ({ expression, weight: 1 }));
      const next = [...prev];
      let cursor = 0;
      for (let index = 0; index < next.length && cursor < additions.length; index += 1) {
        if (!next[index].expression.trim()) next[index] = additions[cursor++];
      }
      while (cursor < additions.length && next.length < 10) next.push(additions[cursor++]);
      return next;
    });
  };

  const validForAttribution = factors.filter((factor) => factor.expression.trim());

  return (
    <div className="space-y-4">
      <ErrorNotice message={error} onClear={() => setError(null)} />
      <FactorExpressionRows
        rows={factors}
        onChange={setFactors}
        mode="weighted"
        minRows={2}
        maxRows={10}
        savedExpressions={savedExpressions}
      />
      <button type="button" onClick={() => setPickerOpen(true)} className="inline-flex items-center gap-1.5 text-xs text-amber-600 hover:text-amber-700">
        <Star className="h-3.5 w-3.5" />
        从因子库选择
      </button>
      <FactorLibraryPicker
        open={pickerOpen}
        title="从因子库选择"
        existingExpressions={factors.map((factor) => factor.expression)}
        onClose={() => setPickerOpen(false)}
        onConfirm={appendExpressions}
      />

      <div className="flex gap-2">
        {METHODS.map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => setMethod(item.value)}
            className={`flex-1 rounded-lg px-3 py-2 text-xs font-medium transition-colors ${method === item.value ? "bg-blue-50 text-blue-700 ring-1 ring-blue-200" : "bg-gray-50 text-gray-500 hover:bg-gray-100"}`}
            title={item.desc}
          >
            <Shuffle className="mr-1 inline h-3 w-3" />
            {item.label}
          </button>
        ))}
      </div>

      <FactorBacktestSettings value={settings} onChange={setSettings} />

      <LoadingButton
        type="button"
        onClick={() => void handleSubmit()}
        loading={isLoading}
        disabled={factors.filter((factor) => factor.expression.trim()).length < 2}
        icon={<Play className="h-4 w-4" />}
        className="w-full bg-blue-600 hover:bg-blue-700"
      >
        {isLoading ? "组合回测中..." : "开始组合回测"}
      </LoadingButton>

      {validForAttribution.length >= 2 && (
        <AttributionChart
          factors={validForAttribution.map((factor, index) => ({
            expression: factor.expression,
            weight: factor.weight ?? 1,
            label: factor.label || `Factor_${index + 1}`,
          }))}
          universe={settings.universe}
          startDate={settings.start_date}
          endDate={settings.end_date}
          nGroups={settings.n_groups ?? 5}
          holdingPeriod={settings.holding_period ?? 5}
        />
      )}
    </div>
  );
}
