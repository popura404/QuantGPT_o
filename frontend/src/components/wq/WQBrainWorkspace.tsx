import { useCallback, useEffect, useRef, useState } from "react";
import { streamTask } from "../../api/client";
import {
  checkAlphaStatus,
  checkWQBatchAlphaStatus,
  finalizeWQBatch,
  getWQBrainStatus,
  listSubmittedAlphas,
  listWQPlatformAlphas,
  submitAlphaById,
  submitAlphaFromTask,
  submitWQBatch,
  submitWQBatchById,
  submitWQBrainSimulation,
  WQBrainApiError,
} from "../../api/wqBrain";
import type { SubmissionPreflight, Task, WQBrainTaskResult } from "../../types/backtest";
import type {
  WQBrainSimulationPayload,
  WQBrainStatus,
  WQPlatformAlpha,
  WQSubmitResponse,
  WQBatchSubmitPayload,
  WQBatchSubmitByIdPayload,
  WQAccount,
} from "../../types/wqBrain";
import WQBrainAlphaTable from "./WQBrainAlphaTable";
import WQBrainBatchSubmitPanel from "./WQBrainBatchSubmitPanel";
import WQBrainDirectSubmitPanel from "./WQBrainDirectSubmitPanel";
import WQBrainStatusCard from "./WQBrainStatusCard";
import WQBrainSubmitForm from "./WQBrainSubmitForm";
import WQBrainTaskPanel from "./WQBrainTaskPanel";

function preflightFromError(err: unknown): WQSubmitResponse | null {
  if (!(err instanceof WQBrainApiError) || !err.detail || typeof err.detail !== "object") return null;
  const detail = err.detail as { error_code?: string; message?: string; submission_preflight?: SubmissionPreflight };
  if (detail.error_code !== "LOCAL_PREFLIGHT_BLOCKED" && !detail.submission_preflight) return null;
  return {
    ok: false,
    error: detail.error_code,
    detail: detail.message,
    submission_preflight: detail.submission_preflight,
  };
}

export default function WQBrainWorkspace() {
  const [status, setStatus] = useState<WQBrainStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [task, setTask] = useState<Task | null>(null);
  const [taskLoading, setTaskLoading] = useState(false);
  const [submitLoading, setSubmitLoading] = useState(false);
  const [directResult, setDirectResult] = useState<WQSubmitResponse | null>(null);
  const [formalResult, setFormalResult] = useState<WQBrainTaskResult | null>(null);
  const [platformAlphas, setPlatformAlphas] = useState<WQPlatformAlpha[]>([]);
  const [submittedAlphas, setSubmittedAlphas] = useState<WQPlatformAlpha[]>([]);
  const [alphaLoading, setAlphaLoading] = useState(false);
  const [batchOutput, setBatchOutput] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<(() => void) | null>(null);

  const refreshStatus = useCallback(async () => {
    setStatusLoading(true);
    setError(null);
    try {
      setStatus(await getWQBrainStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : "WQ 状态读取失败");
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    return () => closeRef.current?.();
  }, [refreshStatus]);

  const watchTask = useCallback((taskId: string, taskType: Task["task_type"]) => {
    closeRef.current?.();
    const initial: Task = { task_id: taskId, status: "pending", task_type: taskType };
    setTask(initial);
    closeRef.current = streamTask(
      taskId,
      (next) => {
        setTask(next);
        if (["completed", "failed", "cancelled"].includes(String(next.status))) setTaskLoading(false);
      },
      () => setTaskLoading(false),
      (message) => setError(message),
    );
  }, []);

  async function handleSimulation(payload: WQBrainSimulationPayload) {
    setTaskLoading(true);
    setFormalResult(null);
    setError(null);
    try {
      const response = await submitWQBrainSimulation(payload);
      watchTask(response.task_id, "wq_brain_submit");
    } catch (err) {
      setTaskLoading(false);
      setError(err instanceof Error ? err.message : "WQ 模拟提交失败");
    }
  }

  async function handleFormalSubmit(taskId: string, reason?: string | null) {
    setSubmitLoading(true);
    setError(null);
    setFormalResult(null);
    try {
      setFormalResult(await submitAlphaFromTask(taskId, reason));
    } catch (err) {
      const structured = preflightFromError(err);
      if (structured) setFormalResult(structured);
      else setError(err instanceof Error ? err.message : "正式提交失败");
    } finally {
      setSubmitLoading(false);
    }
  }

  async function handleDirectSubmit(alphaId: string, account: string, expression?: string | null, reason?: string | null) {
    setSubmitLoading(true);
    setDirectResult(null);
    setError(null);
    try {
      setDirectResult(await submitAlphaById(alphaId, account, expression, reason));
    } catch (err) {
      const structured = preflightFromError(err);
      if (structured) setDirectResult(structured);
      else setError(err instanceof Error ? err.message : "按 alpha_id 提交失败");
    } finally {
      setSubmitLoading(false);
    }
  }

  async function loadPlatform(account: string) {
    setAlphaLoading(true);
    setError(null);
    try {
      const data = await listWQPlatformAlphas(account);
      setPlatformAlphas(data.alphas);
    } catch (err) {
      setError(err instanceof Error ? err.message : "platform alpha 查询失败");
    } finally {
      setAlphaLoading(false);
    }
  }

  async function loadSubmitted() {
    setAlphaLoading(true);
    setError(null);
    try {
      const data = await listSubmittedAlphas();
      setSubmittedAlphas(data.alphas);
    } catch (err) {
      setError(err instanceof Error ? err.message : "local alpha 查询失败");
    } finally {
      setAlphaLoading(false);
    }
  }

  async function handleCheckAlpha(alphaId: string, account: string) {
    setAlphaLoading(true);
    setError(null);
    try {
      setBatchOutput(await checkAlphaStatus(alphaId, account));
    } catch (err) {
      setError(err instanceof Error ? err.message : "alpha 状态查询失败");
    } finally {
      setAlphaLoading(false);
    }
  }

  async function handleBatchSubmit(payload: WQBatchSubmitPayload) {
    setTaskLoading(true);
    setError(null);
    try {
      const response = await submitWQBatch(payload);
      setBatchOutput(response as unknown as Record<string, unknown>);
      watchTask(response.task_id, "wq_brain_batch");
    } catch (err) {
      setTaskLoading(false);
      setError(err instanceof Error ? err.message : "批量模拟失败");
    }
  }

  async function handleBatchSubmitById(payload: WQBatchSubmitByIdPayload) {
    setTaskLoading(true);
    setError(null);
    try {
      const response = await submitWQBatchById(payload);
      setBatchOutput(response as unknown as Record<string, unknown>);
      watchTask(response.task_id, "wq_brain_batch_submit_by_id");
    } catch (err) {
      const structured = preflightFromError(err);
      if (structured) setBatchOutput(structured as unknown as Record<string, unknown>);
      else setError(err instanceof Error ? err.message : "批量提交失败");
      setTaskLoading(false);
    }
  }

  async function handleBatchStatus(alphaIds: string[], account: string) {
    setAlphaLoading(true);
    setError(null);
    try {
      setBatchOutput(await checkWQBatchAlphaStatus({ alpha_ids: alphaIds }, account));
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量状态查询失败");
    } finally {
      setAlphaLoading(false);
    }
  }

  async function handleFinalize(alphaIds: string[], account: WQAccount) {
    setAlphaLoading(true);
    setError(null);
    try {
      setBatchOutput(await finalizeWQBatch({ alpha_ids: alphaIds, account }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "finalize 失败");
    } finally {
      setAlphaLoading(false);
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">WQ BRAIN</h2>
          <p className="text-sm text-gray-500">模拟、预检、正式提交和 SC 状态收敛</p>
        </div>
      </div>
      {error && <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <WQBrainStatusCard status={status} loading={statusLoading} onRefresh={refreshStatus} />
          <WQBrainTaskPanel
            task={task}
            submitting={submitLoading}
            submitResult={formalResult}
            onFormalSubmit={handleFormalSubmit}
          />
        </div>
        <div className="space-y-4">
          <WQBrainSubmitForm loading={taskLoading} onSubmit={(payload) => void handleSimulation(payload)} />
          <WQBrainDirectSubmitPanel loading={submitLoading} result={directResult} onSubmit={(alphaId, account, expression, reason) => void handleDirectSubmit(alphaId, account, expression, reason)} />
          <WQBrainBatchSubmitPanel
            loading={taskLoading || alphaLoading}
            output={batchOutput}
            onBatchSubmit={(payload) => void handleBatchSubmit(payload)}
            onBatchSubmitById={(payload) => void handleBatchSubmitById(payload)}
            onCheckStatus={(alphaIds, account) => void handleBatchStatus(alphaIds, account)}
            onFinalize={(alphaIds, account) => void handleFinalize(alphaIds, account)}
          />
          <WQBrainAlphaTable
            platformAlphas={platformAlphas}
            submittedAlphas={submittedAlphas}
            loading={alphaLoading}
            onLoadPlatform={(account) => void loadPlatform(account)}
            onLoadSubmitted={() => void loadSubmitted()}
            onCheckStatus={(alphaId, account) => void handleCheckAlpha(alphaId, account)}
          />
        </div>
      </div>
    </section>
  );
}
