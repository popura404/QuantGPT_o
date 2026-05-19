import { useState } from "react";
import { CheckCircle2, Send } from "lucide-react";
import type { WQAccount, WQBatchSubmitByIdPayload, WQBatchSubmitPayload } from "../../types/wqBrain";

function parseList(value: string): string[] {
  return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}

function parseExpressionMap(value: string): Record<string, string> | null {
  const entries = value.split(/\n/).map((line) => line.trim()).filter(Boolean);
  if (entries.length === 0) return null;
  const result: Record<string, string> = {};
  entries.forEach((line) => {
    const [id, ...rest] = line.split("=");
    if (id?.trim() && rest.join("=").trim()) result[id.trim()] = rest.join("=").trim();
  });
  return Object.keys(result).length > 0 ? result : null;
}

interface Props {
  loading: boolean;
  output: Record<string, unknown> | null;
  onBatchSubmit: (payload: WQBatchSubmitPayload) => void;
  onBatchSubmitById: (payload: WQBatchSubmitByIdPayload) => void;
  onCheckStatus: (alphaIds: string[], account: string) => void;
  onFinalize: (alphaIds: string[], account: WQAccount) => void;
}

export default function WQBrainBatchSubmitPanel({ loading, output, onBatchSubmit, onBatchSubmitById, onCheckStatus, onFinalize }: Props) {
  const [expression, setExpression] = useState("");
  const [tag, setTag] = useState("frontend-batch");
  const [alphaIds, setAlphaIds] = useState("");
  const [expressions, setExpressions] = useState("");
  const [reason, setReason] = useState("");
  const [account, setAccount] = useState<WQAccount>("primary");

  const ids = parseList(alphaIds);

  return (
    <section className="space-y-4 rounded-xl border border-gray-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-gray-900">批量操作</h3>
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="space-y-2">
          <div className="text-xs font-medium text-gray-500">参数扫描模拟</div>
          <textarea value={expression} onChange={(event) => setExpression(event.target.value)} rows={3} placeholder="expression" className="w-full rounded-md border border-gray-200 px-3 py-2 font-mono text-xs" />
          <input value={tag} onChange={(event) => setTag(event.target.value)} className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm" />
          <button
            type="button"
            disabled={loading || !expression.trim()}
            onClick={() => onBatchSubmit({
              expression: expression.trim(),
              tag: tag.trim() || "frontend-batch",
              regions: ["USA"],
              delays: [1],
              universes: ["TOP3000", "TOP1000"],
              neutralizations: ["SUBINDUSTRY", "INDUSTRY"],
              decay: 0,
              truncation: 0.08,
              account,
              auto_submit: false,
              submission_override_reason: reason || null,
            })}
            className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
            批量模拟
          </button>
        </div>
        <div className="space-y-2">
          <div className="text-xs font-medium text-gray-500">按 ID 批量提交 / 状态 / finalize</div>
          <select value={account} onChange={(event) => setAccount(event.target.value as WQAccount)} className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm">
            <option value="primary">primary</option>
            <option value="alt">alt</option>
          </select>
          <textarea value={alphaIds} onChange={(event) => setAlphaIds(event.target.value)} rows={3} placeholder="alpha ids, comma or newline separated" className="w-full rounded-md border border-gray-200 px-3 py-2 font-mono text-xs" />
          <textarea value={expressions} onChange={(event) => setExpressions(event.target.value)} rows={3} placeholder="optional: alpha_id=expression" className="w-full rounded-md border border-gray-200 px-3 py-2 font-mono text-xs" />
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={2} placeholder="submission_override_reason" className="w-full rounded-md border border-gray-200 px-3 py-2 text-xs" />
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={loading || ids.length === 0} onClick={() => onBatchSubmitById({ alpha_ids: ids, account, expressions_by_alpha_id: parseExpressionMap(expressions), submission_override_reason: reason || null })} className="inline-flex items-center gap-2 rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
              <Send className="h-4 w-4" />
              批量提交
            </button>
            <button type="button" disabled={loading || ids.length === 0} onClick={() => onCheckStatus(ids, account)} className="inline-flex items-center gap-2 rounded-md border border-gray-300 px-3 py-2 text-sm">
              状态
            </button>
            <button type="button" disabled={loading || ids.length === 0} onClick={() => onFinalize(ids, account)} className="inline-flex items-center gap-2 rounded-md border border-gray-300 px-3 py-2 text-sm">
              <CheckCircle2 className="h-4 w-4" />
              finalize
            </button>
          </div>
        </div>
      </div>
      {output && <pre className="max-h-72 overflow-auto rounded-md bg-gray-50 p-3 text-xs">{JSON.stringify(output, null, 2)}</pre>}
    </section>
  );
}
