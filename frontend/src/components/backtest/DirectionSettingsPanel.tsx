import type { DirectionMode } from "../../types/backtest";
import { useColorMode } from "../../contexts/ColorModeContext";

interface Props {
  oosEnabled: boolean;
  directionMode: DirectionMode;
  fixedDirection: 1 | -1 | null;
  onChange: (directionMode: DirectionMode, fixedDirection: 1 | -1 | null) => void;
}

export default function DirectionSettingsPanel({ oosEnabled, directionMode, fixedDirection, onChange }: Props) {
  const { isDark } = useColorMode();
  const disabled = oosEnabled;

  return (
    <section className={`rounded-lg border p-3 ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-100 bg-gray-50"}`}>
      <div className="text-sm font-medium">方向策略</div>
      {disabled && (
        <p className={`mt-1 text-xs ${isDark ? "text-gray-400" : "text-gray-500"}`}>
          OOS 启用时固定提交 auto_full，后端在训练期确定方向，验证/测试不做事后翻转。
        </p>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2">
        <label className="block">
          <span className="text-xs text-gray-500">direction_mode</span>
          <select
            value={disabled ? "auto_full" : directionMode}
            disabled={disabled}
            onChange={(event) => {
              const next = event.target.value as DirectionMode;
              onChange(next, next === "fixed" ? 1 : null);
            }}
            className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm disabled:opacity-60 ${
              isDark ? "border-gray-700 bg-gray-800 text-gray-100" : "border-gray-200 bg-white"
            }`}
          >
            <option value="auto_full">auto_full</option>
            <option value="fixed">fixed</option>
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-gray-500">fixed_direction</span>
          <select
            value={disabled || directionMode !== "fixed" ? "" : String(fixedDirection ?? 1)}
            disabled={disabled || directionMode !== "fixed"}
            onChange={(event) => onChange("fixed", Number(event.target.value) as 1 | -1)}
            className={`mt-1 block w-full rounded-lg border px-3 py-2 text-sm disabled:opacity-60 ${
              isDark ? "border-gray-700 bg-gray-800 text-gray-100" : "border-gray-200 bg-white"
            }`}
          >
            <option value="">无</option>
            <option value="1">1 高值做多</option>
            <option value="-1">-1 低值做多</option>
          </select>
        </label>
      </div>
    </section>
  );
}
