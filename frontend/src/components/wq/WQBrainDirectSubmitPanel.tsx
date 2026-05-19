import { useState } from "react";
import { Send } from "lucide-react";
import type { WQSubmitResponse } from "../../types/wqBrain";
import WQBrainPreflightPanel from "./WQBrainPreflightPanel";

interface Props {
  loading: boolean;
  result: WQSubmitResponse | null;
  onSubmit: (alphaId: string, account: string, expression?: string | null, reason?: string | null) => void;
}

export default function WQBrainDirectSubmitPanel({ loading, result, onSubmit }: Props) {
  const [alphaId, setAlphaId] = useState("");
  const [account, setAccount] = useState("primary");
  const [expression, setExpression] = useState("");
  const [reason, setReason] = useState("");

  return (
    <section className="space-y-3 rounded-xl border border-gray-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-gray-900">按 alpha_id 正式提交</h3>
      <div className="grid gap-2 md:grid-cols-[1fr_140px]">
        <input value={alphaId} onChange={(event) => setAlphaId(event.target.value)} placeholder="alpha_id" className="rounded-md border border-gray-200 px-3 py-2 text-sm" />
        <select value={account} onChange={(event) => setAccount(event.target.value)} className="rounded-md border border-gray-200 px-3 py-2 text-sm">
          <option value="primary">primary</option>
          <option value="alt">alt</option>
        </select>
      </div>
      <textarea value={expression} onChange={(event) => setExpression(event.target.value)} placeholder="expression provenance" rows={2} className="w-full rounded-md border border-gray-200 px-3 py-2 font-mono text-xs" />
      <textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="override reason" rows={2} className="w-full rounded-md border border-gray-200 px-3 py-2 text-xs" />
      <button type="button" onClick={() => onSubmit(alphaId.trim(), account, expression || null, reason || null)} disabled={loading || !alphaId.trim()} className="inline-flex items-center gap-2 rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
        <Send className="h-4 w-4" />
        提交
      </button>
      {result?.submission_preflight && <WQBrainPreflightPanel preflight={result.submission_preflight} />}
      {result && <pre className="max-h-52 overflow-auto rounded-md bg-gray-50 p-3 text-xs">{JSON.stringify(result, null, 2)}</pre>}
    </section>
  );
}
