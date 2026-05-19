import { useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import type { WQPlatformAlpha } from "../../types/wqBrain";

interface Props {
  platformAlphas: WQPlatformAlpha[];
  submittedAlphas: WQPlatformAlpha[];
  loading: boolean;
  onLoadPlatform: (account: string) => void;
  onLoadSubmitted: () => void;
  onCheckStatus: (alphaId: string, account: string) => void;
}

export default function WQBrainAlphaTable({ platformAlphas, submittedAlphas, loading, onLoadPlatform, onLoadSubmitted, onCheckStatus }: Props) {
  const [account, setAccount] = useState("primary");
  const rows = [...platformAlphas.map((a) => ({ ...a, source: "platform" })), ...submittedAlphas.map((a) => ({ ...a, source: "local" }))];

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900">Alpha 查询</h3>
        <div className="flex items-center gap-2">
          <select value={account} onChange={(event) => setAccount(event.target.value)} className="h-8 rounded-md border border-gray-300 px-2 text-xs">
            <option value="primary">primary</option>
            <option value="alt">alt</option>
          </select>
          <button type="button" onClick={() => onLoadPlatform(account)} className="inline-flex h-8 items-center gap-1 rounded-md border border-gray-300 px-2 text-xs">
            <Search className="h-3.5 w-3.5" />
            platform
          </button>
          <button type="button" onClick={onLoadSubmitted} className="inline-flex h-8 items-center gap-1 rounded-md border border-gray-300 px-2 text-xs">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            local
          </button>
        </div>
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-3 py-2">source</th>
              <th className="px-3 py-2">alpha</th>
              <th className="px-3 py-2">status</th>
              <th className="px-3 py-2">grade</th>
              <th className="px-3 py-2 text-right">action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.length === 0 && <tr><td colSpan={5} className="px-3 py-8 text-center text-gray-400">暂无 alpha</td></tr>}
            {rows.map((alpha, index) => {
              const alphaId = String(alpha.alpha_id ?? alpha.id ?? "");
              return (
                <tr key={`${alpha.source}-${alphaId || index}`}>
                  <td className="px-3 py-2 text-gray-500">{alpha.source}</td>
                  <td className="px-3 py-2 font-mono text-gray-900">{alphaId || "-"}</td>
                  <td className="px-3 py-2">{String(alpha.status ?? (alpha as Record<string, unknown>).final_status ?? "-")}</td>
                  <td className="px-3 py-2">{String(alpha.grade ?? "-")}</td>
                  <td className="px-3 py-2 text-right">
                    {alphaId && (
                      <button type="button" onClick={() => onCheckStatus(alphaId, account)} className="rounded-md border border-gray-300 px-2 py-1">
                        状态
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
