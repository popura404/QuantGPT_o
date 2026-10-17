import { useResearchValidation } from "../hooks/useResearchValidation";
import TaskProgressPanel from "./tasks/TaskProgressPanel";

const CHECK_NAMES: Record<string, string> = {
  definition_config: "冻结因子与配置", replayable_data: "快照可重放", data_quality: "行情字段质量",
  simulation: "冻结仿真重放", costs: "冻结费用重放", label_boundaries: "标签与窗口边界",
  oos: "独立样本外证据", walk_forward: "前向窗口重放", placebo: "安慰剂诊断",
  trial_correction: "多次试验校正", similarity: "同类因子对照", risk: "组合约束",
};
const STATUS_NAMES: Record<string, string> = { passed: "通过", failed: "未通过", insufficient_data: "证据不足", diagnostic: "仅诊断" };
const BLOCKER_NAMES: Record<string, string> = {
  FINAL_TEST_REQUIRED: "尚缺预登记最终测试", EVIDENCE_NOT_VERIFIED: "研究证据尚未完成验收",
  OFFLINE_VALIDATION_STALE_TRIAL_COUNT: "项目试验次数已变更，请重新运行校正",
  EXTERNAL_DATA_ACCEPTANCE_REQUIRED: "真实数据验收未完成", HISTORICAL_UNIVERSE_UNVERIFIED: "历史证券池尚未验收",
  CORPORATE_ACTION_COVERAGE_UNVERIFIED: "公司行为覆盖尚未验收", TOTAL_RETURN_BENCHMARK_UNVERIFIED: "全收益基准尚未验收",
};

export default function OfflineValidationPanel({ projectId, evaluationId, selectionEvaluationId, disabled }: {
  projectId: string;
  evaluationId: string;
  selectionEvaluationId?: string;
  disabled: boolean;
}) {
  const { task, profile, busy, error, start } = useResearchValidation(projectId, evaluationId, selectionEvaluationId);
  const blockers = [...new Set([...(profile?.blockers ?? []), ...(profile?.external_blockers ?? [])])];
  return <section aria-label="因子离线验证" className="space-y-3 rounded border border-blue-200 bg-blue-50 p-3">
    <h4 className="text-sm font-medium">{profile?.offline_validation ? "离线验证结果已保存" : "离线验证"}</h4>
    <p className="text-xs text-gray-600">从冻结快照重放并检查费用、窗口与试验校正。离线计算完成不代表真实数据验收通过，不能据此发布或解锁导出。</p>
    <button disabled={disabled || busy} onClick={() => void start()} className="rounded border border-blue-300 bg-white px-3 py-2 text-sm disabled:opacity-50">
      {busy ? "正在读取或运行离线验证…" : profile?.offline_validation ? "重新运行离线验证" : "运行离线验证"}
    </button>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {task && <TaskProgressPanel task={task} />}
    {profile && <>
      {Object.keys(profile.checks).length > 0 && <table aria-label="离线验证检查" className="w-full text-left text-xs"><thead><tr><th>检查项</th><th>结果</th></tr></thead><tbody>
        {Object.entries(profile.checks).map(([name, check]) => <tr key={name}><td className="py-1">{CHECK_NAMES[name] ?? name}</td><td>{STATUS_NAMES[check.status] ?? check.status}</td></tr>)}
      </tbody></table>}
      <p className="text-xs font-medium text-amber-900">真实数据验收仍未完成，发布与导出条件仍需核对。</p>
      {blockers.length > 0 && <ul className="list-inside list-disc space-y-1 text-xs text-amber-900">{blockers.map((blocker) => <li key={blocker}>
        {blocker.startsWith("REQUIRED_EVIDENCE:") ? `缺少证据：${CHECK_NAMES[blocker.split(":")[1]] ?? blocker.split(":")[1]}` : BLOCKER_NAMES[blocker] ?? blocker}
      </li>)}</ul>}
      <details className="text-xs text-gray-600"><summary>验证证据与不可变报告引用</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap">{JSON.stringify(profile, null, 2)}</pre></details>
    </>}
  </section>;
}
