import { AlertCircle } from "lucide-react";

export default function ErrorNotice({ message, onClear }: { message: string | null; onClear?: () => void }) {
  if (!message) return null;
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      <div className="flex items-start gap-2">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
        <span>{message}</span>
      </div>
      {onClear && (
        <button type="button" onClick={onClear} className="text-xs font-medium text-red-600">
          关闭
        </button>
      )}
    </div>
  );
}
