import type { BacktestResult } from "../types/backtest";
import { type ReactNode } from "react";
import { Star } from "lucide-react";
import MetricCard from "./MetricCard";
import GroupReturnsTable from "./GroupReturnsTable";
import ReportViewer from "./ReportViewer";
import StockFactorPanel from "./StockFactorPanel";
import FactorInterpretationCard from "./FactorInterpretationCard";
import ShareCardButton from "./ShareCardButton";
import WQBrainCard from "./WQBrainCard";
import { useColorMode } from "../contexts/ColorModeContext";

interface Props {
  result: BacktestResult;
  iterationSlot?: ReactNode;
  onSaveFactor?: () => void;
  isSaving?: boolean;
  isSaved?: boolean;
}

function pct(n: number): string {
  return (n * 100).toFixed(2) + "%";
}

function num(n: number): string {
  return n.toFixed(4);
}

function metricNumber(metrics: Record<string, number | string | null> | undefined, key: string): number {
  const value = metrics?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export default function ResultsDashboard({ result, iterationSlot, onSaveFactor, isSaving, isSaved }: Props) {
  const { isDark } = useColorMode();
  const { metrics, backtest_summary, report_url, params } = result;
  const oos = result.oos_result;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className={`text-sm font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>回测结果</h3>
        <div className="flex items-center gap-3">
          <ShareCardButton result={result} />
          {onSaveFactor && (
            <button
              onClick={onSaveFactor}
              disabled={isSaving || isSaved}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50 ${
                isSaved
                  ? "text-amber-600 bg-amber-50 cursor-default"
                  : "text-amber-700 bg-amber-50 hover:bg-amber-100"
              }`}
            >
              <Star className={`h-3.5 w-3.5 ${isSaved ? "fill-amber-400" : ""}`} />
              {isSaved ? "已收藏" : isSaving ? "保存中..." : "收藏因子"}
            </button>
          )}
          <span className="text-xs text-gray-400">
            {params.universe} · {params.start_date} ~ {params.end_date} · {params.stock_count} 只股票
          </span>
        </div>
      </div>

      <div className={`rounded-xl border ${isDark ? "border-gray-700" : "border-gray-200"} ${isDark ? "bg-gray-900" : "bg-white"} px-4 py-3`}>
        <p className={`text-xs font-medium ${isDark ? "text-gray-400" : "text-gray-500"} mb-1`}>因子表达式</p>
        <code className={`text-sm ${isDark ? "text-amber-400" : "text-blue-700"} font-mono break-all leading-relaxed`}>{params.expression}</code>
      </div>

      {result.interpretation && Object.keys(result.interpretation).length > 0 && (
        <FactorInterpretationCard interpretation={result.interpretation} />
      )}

      {oos && (
        <div className={`rounded-xl border ${isDark ? "border-emerald-800 bg-emerald-950/30" : "border-emerald-200 bg-emerald-50"} p-4`}>
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
          <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
            {(["train", "valid", "test"] as const).map((key) => {
              const period = oos[key]?.period?.join(" ~ ") ?? "-";
              const periodMetrics = oos[key]?.metrics;
              return (
                <div key={key} className={`rounded-md p-3 ${isDark ? "bg-gray-900" : "bg-white"}`}>
                  <div className={`font-medium ${isDark ? "text-gray-200" : "text-gray-900"}`}>{key}</div>
                  <div className={`mt-1 ${isDark ? "text-gray-400" : "text-gray-500"}`}>{period}</div>
                  <div className="mt-2 grid grid-cols-2 gap-1 font-mono">
                    <span>Sharpe {num(metricNumber(periodMetrics, "long_short_sharpe"))}</span>
                    <span>IC {num(metricNumber(periodMetrics, "direction_adjusted_rank_ic_mean"))}</span>
                    <span>Turn {pct(metricNumber(periodMetrics, "turnover"))}</span>
                    <span>DD {pct(metricNumber(periodMetrics, "max_drawdown"))}</span>
                  </div>
                </div>
              );
            })}
          </div>
          {oos.data_quality && (
            <div className={`mt-3 text-xs ${isDark ? "text-emerald-200" : "text-emerald-800"}`}>
              数据质量: dropped_rows={String(oos.data_quality.dropped_rows ?? 0)} · adjustment={String(oos.data_quality.adjustment ?? "unknown")}
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard label="总收益" value={pct(metrics.total_return)} color={metrics.total_return >= 0 ? "green" : "red"} sub={metrics.benchmark_total_return != null ? pct(metrics.benchmark_total_return) : undefined} />
        <MetricCard label="年化收益" value={pct(metrics.cagr)} color={metrics.cagr >= 0 ? "green" : "red"} sub={metrics.benchmark_cagr != null ? pct(metrics.benchmark_cagr) : undefined} />
        <MetricCard label="Sharpe" value={num(metrics.sharpe)} color={metrics.sharpe >= 1 ? "green" : "default"} />
        <MetricCard label="Sortino" value={num(metrics.sortino)} />
        <MetricCard label="最大回撤" value={pct(metrics.max_drawdown)} color="red" />
        <MetricCard label="波动率" value={pct(metrics.volatility)} />
        <MetricCard label="胜率" value={pct(metrics.win_rate)} />
        <MetricCard label="盈亏比" value={num(metrics.profit_factor)} />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard label="多头 Sharpe" value={num(backtest_summary.top_group_sharpe ?? backtest_summary.long_short_sharpe)} color={(backtest_summary.top_group_sharpe ?? backtest_summary.long_short_sharpe) >= 1 ? "green" : "default"} />
        <MetricCard label="多空年化" value={pct(backtest_summary.long_short_annual ?? 0)} color={(backtest_summary.long_short_annual ?? 0) >= 0 ? "green" : "red"} />
        <MetricCard label="单调性" value={num(backtest_summary.monotonicity_score)} color={backtest_summary.monotonicity_score >= 0.8 ? "green" : "default"} />
        <MetricCard label="分组价差" value={pct(backtest_summary.spread)} color={backtest_summary.spread >= 0 ? "green" : "red"} />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard label="IC 均值" value={num(backtest_summary.ic_mean ?? 0)} color={(backtest_summary.ic_mean ?? 0) > 0.03 ? "green" : (backtest_summary.ic_mean ?? 0) < -0.03 ? "red" : "default"} />
        <MetricCard label="Rank IC" value={num(backtest_summary.rank_ic_mean ?? 0)} color={(backtest_summary.rank_ic_mean ?? 0) > 0.03 ? "green" : (backtest_summary.rank_ic_mean ?? 0) < -0.03 ? "red" : "default"} />
        <MetricCard label="IR (IC/std)" value={num(backtest_summary.ic_ir ?? 0)} color={Math.abs(backtest_summary.ic_ir ?? 0) >= 0.5 ? "green" : "default"} />
        <MetricCard label="IC 胜率" value={pct(backtest_summary.ic_win_rate ?? 0)} color={(backtest_summary.ic_win_rate ?? 0) >= 0.55 ? "green" : "default"} />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard label="换手率" value={pct(backtest_summary.turnover ?? 0)} />
        <MetricCard label="WQ Fitness" value={num(backtest_summary.wq_fitness ?? 0)} color={(backtest_summary.wq_fitness ?? 0) >= 1.0 ? "green" : "default"} />
      </div>

      {result.wq_brain && Object.keys(result.wq_brain.wq_is_tests ?? {}).length > 0 && (
        <WQBrainCard wqBrain={result.wq_brain} />
      )}

      {iterationSlot}

      {result.stock_factor_data && (
        <StockFactorPanel
          data={result.stock_factor_data}
          expression={params.expression}
          topGroupAnnualReturn={
            Object.values(backtest_summary.group_returns)
              .reduce((best, g) => g.annual_return > best ? g.annual_return : best, -Infinity)
          }
        />
      )}

      <GroupReturnsTable groupReturns={backtest_summary.group_returns} />
      <ReportViewer reportUrl={report_url} />
    </div>
  );
}
