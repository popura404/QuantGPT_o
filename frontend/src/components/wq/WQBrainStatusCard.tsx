import { RefreshCw } from "lucide-react";
import type { WQBrainStatus } from "../../types/wqBrain";

interface Props {
  status: WQBrainStatus | null;
  loading: boolean;
  onRefresh: () => void;
}

export default function WQBrainStatusCard({ status, loading, onRefresh }: Props) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">WQ BRAIN 配置</h3>
          <p className="mt-1 text-xs text-gray-500">
            {status?.configured ? "已配置 server-side 账号" : "未检测到 server-side 账号"}
          </p>
        </div>
        <button type="button" onClick={onRefresh} className="rounded-md border border-gray-300 p-2 text-gray-600">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        {(status?.accounts ?? []).map((account) => (
          <span key={account} className="rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-blue-700">{account}</span>
        ))}
        {(status?.accounts ?? []).length === 0 && <span className="text-gray-400">无可用账号</span>}
      </div>
      {status?.thresholds && (
        <pre className="mt-3 max-h-36 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-gray-700">
          {JSON.stringify(status.thresholds, null, 2)}
        </pre>
      )}
    </section>
  );
}
