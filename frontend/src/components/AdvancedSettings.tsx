import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useColorMode } from "../contexts/ColorModeContext";
import type { DataQualityRequest, DirectionMode, OOSRequest, ValidationStage } from "../types/backtest";
import OOSSettingsPanel from "./backtest/OOSSettingsPanel";
import DataQualitySettingsPanel from "./backtest/DataQualitySettingsPanel";
import DirectionSettingsPanel from "./backtest/DirectionSettingsPanel";

export interface AdvancedSettingsValues {
  universe: string;
  universe_date: string | null;
  start_date: string;
  end_date: string;
  n_groups: number;
  holding_period: number;
  benchmark: string;
  neutralize_industry: boolean;
  neutralize_cap: boolean;
  rebalance_anchor: string | null;
  direction_mode: DirectionMode;
  fixed_direction: 1 | -1 | null;
  oos_enabled: boolean;
  oos: OOSRequest;
  validation_stage: ValidationStage;
  data_quality: DataQualityRequest;
}

interface Props {
  values: AdvancedSettingsValues;
  onChange: (values: AdvancedSettingsValues) => void;
}

export default function AdvancedSettings({ values, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const { isDark } = useColorMode();

  const set = <K extends keyof AdvancedSettingsValues>(key: K, val: AdvancedSettingsValues[K]) =>
    onChange({ ...values, [key]: val });

  const setDirection = (directionMode: DirectionMode, fixedDirection: 1 | -1 | null) => {
    onChange({ ...values, direction_mode: directionMode, fixed_direction: fixedDirection });
  };

  const setOosEnabled = (enabled: boolean) => {
    onChange({
      ...values,
      oos_enabled: enabled,
      direction_mode: enabled ? "auto_full" : values.direction_mode,
      fixed_direction: enabled ? null : values.fixed_direction,
      data_quality: enabled ? { ...values.data_quality, enabled: true } : values.data_quality,
    });
  };

  return (
    <div className={`border rounded-xl overflow-hidden ${
      isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-white"
    }`}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={`w-full px-4 py-3 flex items-center justify-between text-sm font-medium transition-colors ${
          isDark
            ? "text-gray-300 hover:bg-gray-800"
            : "text-gray-700 hover:bg-gray-50"
        }`}
      >
        高级设置
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>
                股票池
              </span>
              <select
                value={values.universe}
                onChange={(e) => set("universe", e.target.value)}
                className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                  isDark
                    ? "border-gray-700 bg-gray-800 text-gray-100 focus:ring-blue-500/20 focus:border-blue-500"
                    : "border-gray-200 bg-white focus:ring-blue-500/20 focus:border-blue-500"
                }`}
              >
                <option value="small_scale">small_scale (5只)</option>
                <option value="hs300">沪深300</option>
                <option value="csi500">中证500</option>
                <option value="csi1000">中证1000</option>
                <option value="csi2000">中证2000</option>
              </select>
            </label>
            <label className="block">
              <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>
                基准指数
              </span>
              <select
                value={values.benchmark}
                onChange={(e) => set("benchmark", e.target.value)}
                className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                  isDark
                    ? "border-gray-700 bg-gray-800 text-gray-100 focus:ring-blue-500/20 focus:border-blue-500"
                    : "border-gray-200 bg-white focus:ring-blue-500/20 focus:border-blue-500"
                }`}
              >
                <option value="hs300">沪深300</option>
                <option value="zz500">中证500</option>
                <option value="csi1000">中证1000</option>
                <option value="sz50">上证50</option>
              </select>
            </label>
            <label className="block">
              <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>开始日期</span>
              <input
                type="date"
                value={values.start_date}
                onChange={(e) => set("start_date", e.target.value)}
                className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                  isDark
                    ? "border-gray-700 bg-gray-800 text-gray-100 focus:ring-blue-500/20 focus:border-blue-500"
                    : "border-gray-200 bg-white focus:ring-blue-500/20 focus:border-blue-500"
                }`}
              />
            </label>
            <label className="block">
              <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>结束日期</span>
              <input
                type="date"
                value={values.end_date}
                onChange={(e) => set("end_date", e.target.value)}
                className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                  isDark
                    ? "border-gray-700 bg-gray-800 text-gray-100 focus:ring-blue-500/20 focus:border-blue-500"
                    : "border-gray-200 bg-white focus:ring-blue-500/20 focus:border-blue-500"
                }`}
              />
            </label>
            <label className="block">
              <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>股票池基准日期</span>
              <input
                type="date"
                value={values.universe_date ?? ""}
                onChange={(e) => set("universe_date", e.target.value || null)}
                className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                  isDark
                    ? "border-gray-700 bg-gray-800 text-gray-100 focus:ring-blue-500/20 focus:border-blue-500"
                    : "border-gray-200 bg-white focus:ring-blue-500/20 focus:border-blue-500"
                }`}
              />
            </label>
            <label className="block">
              <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>换仓锚定日期</span>
              <input
                type="date"
                value={values.rebalance_anchor ?? ""}
                onChange={(e) => set("rebalance_anchor", e.target.value || null)}
                className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                  isDark
                    ? "border-gray-700 bg-gray-800 text-gray-100 focus:ring-blue-500/20 focus:border-blue-500"
                    : "border-gray-200 bg-white focus:ring-blue-500/20 focus:border-blue-500"
                }`}
              />
            </label>
            <label className="block">
              <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>分组数量</span>
              <input
                type="number"
                min={2}
                max={20}
                value={values.n_groups}
                onChange={(e) => set("n_groups", Number(e.target.value))}
                className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                  isDark
                    ? "border-gray-700 bg-gray-800 text-gray-100 focus:ring-blue-500/20 focus:border-blue-500"
                    : "border-gray-200 bg-white focus:ring-blue-500/20 focus:border-blue-500"
                }`}
              />
            </label>
            <label className="block">
              <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>
                持仓周期 (交易日)
              </span>
              <input
                type="number"
                min={1}
                max={60}
                value={values.holding_period}
                onChange={(e) => set("holding_period", Number(e.target.value))}
                className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                  isDark
                    ? "border-gray-700 bg-gray-800 text-gray-100 focus:ring-blue-500/20 focus:border-blue-500"
                    : "border-gray-200 bg-white focus:ring-blue-500/20 focus:border-blue-500"
                }`}
              />
            </label>
          </div>

          {/* Neutralization options */}
          <div className={`border-t pt-3 ${isDark ? "border-gray-800" : "border-gray-100"}`}>
            <p className={`text-xs mb-2 ${isDark ? "text-gray-400" : "text-gray-500"}`}>
              因子中性化 <span className={`text-[10px] ${isDark ? "text-gray-600" : "text-gray-400"}`}>（默认开启）</span>
            </p>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={!values.neutralize_industry}
                  onChange={(e) => set("neutralize_industry", !e.target.checked)}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500/20"
                />
                <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-600"}`}>取消行业中性</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={!values.neutralize_cap}
                  onChange={(e) => set("neutralize_cap", !e.target.checked)}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500/20"
                />
                <span className={`text-xs ${isDark ? "text-gray-400" : "text-gray-600"}`}>取消市值中性</span>
              </label>
            </div>
            <p className={`text-[10px] mt-1 ${isDark ? "text-gray-600" : "text-gray-400"}`}>
              中性化可消除行业/市值偏暴露，获得更纯粹的因子 alpha
            </p>
          </div>

          <DirectionSettingsPanel
            oosEnabled={values.oos_enabled}
            directionMode={values.direction_mode}
            fixedDirection={values.fixed_direction}
            onChange={setDirection}
          />

          <OOSSettingsPanel
            enabled={values.oos_enabled}
            value={values.oos}
            onEnabledChange={setOosEnabled}
            onChange={(next) => set("oos", next)}
          />

          <DataQualitySettingsPanel
            value={values.data_quality}
            onChange={(next) => set("data_quality", next)}
          />
        </div>
      )}
    </div>
  );
}
