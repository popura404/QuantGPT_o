# QuantGPT Frontend Completion Plan

> Status: historical implementation plan. The baseline facts below describe the
> frontend gap at the time this plan was written; the current repository already
> includes the shared task components, advanced backtest controls, WQ BRAIN
> workspace, strategy workflow components, and shared factor/common components.

## Purpose

This plan converts the current frontend gap review into an executable frontend work package. It covers five frontend completion tracks:

1. Unified task and result workflow.
2. WQ BRAIN frontend console.
3. Single-factor advanced backtest controls.
4. Strategy Workbench structured workflow.
5. Composite and factor-comparison convergence.

The goal is to make the browser UI independently operable for research, monitoring, WQ BRAIN submission, and strategy review without changing the existing backend contracts unless a listed acceptance item proves an endpoint response is incompatible with its route schema.

## Non-Goals

- Do not add broker integration, order routing, real-money workflow, or execution instructions.
- Do not turn WQ BRAIN into a StrategySpec adapter or strategy acceptance path.
- Do not allow users to enter or store WQ BRAIN credentials in the browser. Credentials remain server-side environment configuration.
- Do not replace the MCP/agent workflow. The frontend must complement it by exposing the same backend task/result state.
- Do not redesign the whole visual system before the functional gaps below are closed.

## Baseline Facts

- Frontend build currently passes with non-blocking Vite bundle warnings: `cd frontend && npm run build`.
- `frontend/src/types/backtest.ts` does not include all backend task statuses and WQ task types.
- `ProgressTracker` is specialized for local single-factor backtests.
- `BacktestForm` and `AdvancedSettings` expose only basic backtest fields even though `BacktestRequest` already includes OOS, data-quality, direction, universe-date, and rebalance-anchor fields.
- `StrategyWorkbench` covers templates, JSON editing, validation, backtest, report, and export, but not saved specs, saved runs, diagnose, strategy anti-overfit, rolling validation, optimize, markets, or data fields.
- WQ BRAIN has backend routes for status, user info, simulation, alpha listing, direct submit, batch submit, alpha status, batch status, finalize, delete, and unhide, but has no dedicated frontend workspace.

## Execution Order

Work in this order. Do not start a later track until the current track builds and its local acceptance checklist passes.

1. Track A: shared task/result foundation.
2. Track B: single-factor advanced controls.
3. Track C: WQ BRAIN console.
4. Track D: Strategy Workbench structured workflow.
5. Track E: composite and comparison convergence.
6. Final hard-stop verification.

## Track A - Unified Task and Result Workflow

### Intent

Create one frontend task workflow for all asynchronous backend jobs. A task from backtest, composite, WQ, or strategy must be viewable through the same status, error, result, and report primitives.

### Files to Change

- `frontend/src/types/backtest.ts`
- `frontend/src/api/client.ts`
- `frontend/src/components/ProgressTracker.tsx`
- `frontend/src/components/ResearchDashboard.tsx`
- `frontend/src/components/TaskHistoryItem.tsx`
- `frontend/src/hooks/useTaskHistory.ts`
- `frontend/src/App.tsx`
- `frontend/src/components/TabNavigation.tsx`

### New Files

- `frontend/src/components/tasks/TaskStatusBadge.tsx`
- `frontend/src/components/tasks/TaskProgressPanel.tsx`
- `frontend/src/components/tasks/TaskResultSummary.tsx`
- `frontend/src/components/tasks/TaskDetailDrawer.tsx`
- `frontend/src/components/tasks/TaskCenter.tsx`

### Required Type Changes

In `frontend/src/types/backtest.ts`:

- Extend `TaskStatus` with backend statuses:
  - `running`
  - `authenticating`
  - `simulating`
  - `submitted`
  - `finalizing`
  - `queued`
- Extend `Task.task_type` with:
  - `wq_brain_submit`
  - `wq_brain_batch`
  - `wq_brain_batch_submit_by_id`
  - `wq_brain_finalize`
- Add optional task fields:
  - `progress?: number`
  - `progress_message?: string`
  - `completed?: number`
  - `completed_combinations?: number`
  - `sub_results?: Record<string, unknown>`
  - `created_at?: string`
  - `completed_at?: string`
  - `duration_seconds?: number`

### Required Behavior

- `TaskStatusBadge` must map every known status to a stable visual state. Unknown statuses must render as neutral text instead of throwing.
- `TaskProgressPanel` must display:
  - task id
  - task type
  - status
  - `progress` when present
  - `progress_message` when present
  - cancel button only for tasks cancelable through `POST /api/v1/tasks/{task_id}/cancel`
- `TaskDetailDrawer` must handle all task types:
  - Backtest result: show expression, core metrics, OOS/data-quality if present, report link.
  - Composite result: show composite expression, core metrics, report link.
  - Strategy result: show score, OOS/data-quality, report link, latest holdings.
  - WQ result: show alpha id, IS metrics, OOS metrics, submit state, `submission_preflight`, platform status fields if present.
  - Failed task: show exact backend error body.
- `TaskCenter` must use `fetchTasks()` with filters for task type and status.
- Keep `ResearchDashboard` but refactor it to use the shared status badge and detail drawer. It must not duplicate task parsing logic that belongs in shared task components.

### Acceptance Checklist

- A completed local backtest task opens in the shared detail drawer and still opens its report.
- A failed task displays the backend error string or JSON detail.
- A strategy task result is not treated as a plain factor backtest.
- WQ task statuses are displayed without falling back to a misleading local backtest progress ladder.
- `cd frontend && npm run build` exits 0.

## Track B - Single-Factor Advanced Backtest Controls

### Intent

Expose existing backend factor-backtest controls in the UI. The user must be able to run the same OOS/data-quality/direction workflow from the browser that is already available through REST/MCP.

### Files to Change

- `frontend/src/components/BacktestForm.tsx`
- `frontend/src/components/AdvancedSettings.tsx`
- `frontend/src/types/backtest.ts`
- `frontend/src/components/ResultsDashboard.tsx`
- `frontend/src/components/ResearchDashboard.tsx`

### New Files

- `frontend/src/components/backtest/OOSSettingsPanel.tsx`
- `frontend/src/components/backtest/DataQualitySettingsPanel.tsx`
- `frontend/src/components/backtest/DirectionSettingsPanel.tsx`
- `frontend/src/components/backtest/BacktestResultOOSCard.tsx`
- `frontend/src/components/backtest/DataQualitySummaryCard.tsx`

### Required Controls

`BacktestForm` state must include these fields and submit them through `submitBacktest()`:

- `universe_date: string | null`
- `rebalance_anchor: string | null`
- `direction_mode: "auto_full" | "fixed"`
- `fixed_direction: 1 | -1 | null`
- `oos_enabled: boolean`
- `oos: OOSRequest | null`
- `data_quality: DataQualityRequest | null`

`OOSSettingsPanel` must expose:

- enabled toggle
- method: `date_ratio` or `date_cut`
- train/valid/test ratios for `date_ratio`
- train_end and valid_end for `date_cut`
- min_train_days, min_valid_days, min_test_days
- warmup_days optional numeric input

`DirectionSettingsPanel` must enforce backend semantics:

- When `oos_enabled=true`, submit `direction_mode="auto_full"` and `fixed_direction=null`.
- When `oos_enabled=false`, allow `auto_full` or `fixed`.
- When `direction_mode="fixed"`, require `fixed_direction` to be exactly `1` or `-1`.

`DataQualitySettingsPanel` must expose:

- enabled toggle
- mode: `report_only`, `filter`, `strict`
- min_price
- max_abs_daily_ret
- max_missing_ratio_per_stock
- require_positive_volume
- require_positive_amount
- drop_st
- drop_new_listing_days
- adjustment
- fail_on_unknown_adjustment

### Required Result Rendering

- `BacktestResultOOSCard` must render train/valid/test periods and key metrics when `result.oos_result` exists.
- `DataQualitySummaryCard` must render dropped rows, dropped stocks, adjustment, scope, warnings, and issues when `result.data_quality` or `result.oos_result.data_quality` exists.
- `ResultsDashboard` must continue to render existing metrics when OOS is disabled.

### Acceptance Checklist

- Submitting with OOS disabled sends no invalid fixed-direction combination.
- Submitting with OOS enabled sends `direction_mode="auto_full"` and `fixed_direction=null`.
- Data-quality disabled while OOS enabled still displays backend warnings when returned.
- Existing simple backtest behavior remains unchanged.
- `cd frontend && npm run build` exits 0.

## Track C - WQ BRAIN Frontend Console

### Intent

Add a dedicated WQ BRAIN workspace that exposes the existing factor workflow without mixing it into StrategySpec. It must support simulation, guarded formal submission, alpha listing, platform status checks, batch submit, and finalize.

### Files to Change

- `frontend/src/App.tsx`
- `frontend/src/components/TabNavigation.tsx`
- `frontend/src/types/backtest.ts`
- `frontend/src/api/client.ts` only if shared task helpers need updates

### New Files

- `frontend/src/api/wqBrain.ts`
- `frontend/src/types/wqBrain.ts`
- `frontend/src/components/wq/WQBrainWorkspace.tsx`
- `frontend/src/components/wq/WQBrainStatusCard.tsx`
- `frontend/src/components/wq/WQBrainSubmitForm.tsx`
- `frontend/src/components/wq/WQBrainTaskPanel.tsx`
- `frontend/src/components/wq/WQBrainAlphaTable.tsx`
- `frontend/src/components/wq/WQBrainDirectSubmitPanel.tsx`
- `frontend/src/components/wq/WQBrainBatchSubmitPanel.tsx`
- `frontend/src/components/wq/WQBrainPreflightPanel.tsx`

### API Functions

Implement these functions in `frontend/src/api/wqBrain.ts`:

- `getWQBrainStatus() -> GET /api/v1/wq-brain/status`
- `getWQBrainUserInfo(account) -> GET /api/v1/wq-brain/user-info?account=...`
- `submitWQBrainSimulation(payload) -> POST /api/v1/wq-brain/submit`
- `listWQPlatformAlphas(account, limit, offset) -> GET /api/v1/wq-brain/platform-alphas`
- `listSubmittedAlphas(limit, offset) -> GET /api/v1/wq-brain/submitted-alphas`
- `submitAlphaFromTask(taskId, submission_override_reason?) -> POST /api/v1/wq-brain/{task_id}/submit-alpha`
- `checkAlphaStatus(alphaId, account) -> GET /api/v1/wq-brain/alpha-status/{alpha_id}`
- `submitAlphaById(alphaId, account, expression?, submission_override_reason?) -> POST /api/v1/wq-brain/submit-by-id/{alpha_id}`
- `deleteAlpha(alphaId, account) -> DELETE /api/v1/wq-brain/alpha/{alpha_id}`
- `unhideAlpha(alphaId, account) -> POST /api/v1/wq-brain/alpha/{alpha_id}/unhide`
- `submitWQBatch(payload) -> POST /api/v1/wq-brain/batch-submit`
- `submitWQBatchById(payload) -> POST /api/v1/wq-brain/batch-submit-by-id`
- `checkWQBatchAlphaStatus(payload, account) -> POST /api/v1/wq-brain/batch-alpha-status`
- `finalizeWQBatch(payload) -> POST /api/v1/wq-brain/batch-finalize`

### Required UI Behavior

- Add `wq` to `MainTab` and `TABS` in `TabNavigation`.
- Render `WQBrainWorkspace` from `App`.
- `WQBrainStatusCard` must show configured state, accounts, and thresholds. It must not display credential values.
- `WQBrainSubmitForm` must include:
  - expression
  - tag
  - region
  - universe
  - delay
  - decay
  - neutralization
  - truncation
  - account
  - auto_submit
  - submission_override_reason
- `auto_submit` must be disabled for `account !== "primary"` with a visible reason.
- `WQBrainPreflightPanel` must display `submission_preflight.allowed`, reasons, warnings, and override state from returned task/result objects.
- Formal submit controls must require an explicit click separate from simulation completion.
- Direct submit by alpha id must require either expression provenance or an override reason when the backend blocks local preflight.
- Batch submit by id must support `expressions_by_alpha_id` and `submission_override_reason`.
- The UI must show SC states: `ACTIVE`, `SC_FAIL`, `SC_PENDING`, `UNSUBMITTED`, `OTHER_FAIL`, and `ERROR`.

### Acceptance Checklist

- A user can submit a WQ simulation and follow the returned task through the shared task workflow.
- A user cannot auto-submit with the `alt` account.
- A backend `LOCAL_PREFLIGHT_BLOCKED` response is rendered as a preflight block, not as a generic crash.
- A user can query platform alphas and submitted local alphas.
- A user can run batch submit by id and finalize pending SC results.
- `cd frontend && npm run build` exits 0.

## Track D - Strategy Workbench Structured Workflow

### Intent

Keep the JSON editor but add structured controls and persistence so the strategy workflow is usable without hand-editing raw JSON for every operation.

### Files to Change

- `frontend/src/api/strategy.ts`
- `frontend/src/types/strategy.ts`
- `frontend/src/components/StrategyWorkbench.tsx`
- `frontend/src/types/backtest.ts`

### New Files

- `frontend/src/components/strategy/StrategyTemplatePicker.tsx`
- `frontend/src/components/strategy/StrategySpecEditor.tsx`
- `frontend/src/components/strategy/StrategyParameterForm.tsx`
- `frontend/src/components/strategy/StrategySpecLibrary.tsx`
- `frontend/src/components/strategy/StrategyRunHistory.tsx`
- `frontend/src/components/strategy/StrategyDiagnosticsPanel.tsx`
- `frontend/src/components/strategy/StrategyValidationPanel.tsx`
- `frontend/src/components/strategy/StrategyResultPanel.tsx`

### API Functions

Extend `frontend/src/api/strategy.ts` with:

- `listStrategyMarkets() -> GET /api/v1/strategy/markets`
- `listStrategyDataFields(market) -> GET /api/v1/strategy/data-fields`
- `getStrategyTemplate(templateId) -> GET /api/v1/strategy/templates/{template_id}`
- `diagnoseStrategy(result) -> POST /api/v1/strategy/diagnose`
- `runStrategyAntiOverfit(result) -> POST /api/v1/strategy/anti-overfit`
- `runStrategyRollingValidation(result, windows) -> POST /api/v1/strategy/rolling-validation`
- `optimizeStrategyCandidate(signals, spec) -> POST /api/v1/strategy/optimize`
- `saveStrategySpec(spec, name, tags) -> POST /api/v1/strategy/specs`
- `listStrategySpecs() -> GET /api/v1/strategy/specs`
- `getStrategySpec(strategyId) -> GET /api/v1/strategy/specs/{strategy_id}`
- `saveStrategyRun(result, strategy_id?, task_id?, report_url?, summary_json?, signal_export?) -> POST /api/v1/strategy/runs`
- `listStrategyRuns(strategy_id?) -> GET /api/v1/strategy/runs`

### Required UI Behavior

- `StrategyTemplatePicker` must show template name, description, risk label, and parameter bounds.
- `StrategyParameterForm` must allow editing the common template parameters without direct JSON edits.
- `StrategySpecEditor` must retain raw JSON editing and validation for advanced users.
- `StrategySpecLibrary` must save and load StrategySpecs through `/strategy/specs`.
- `StrategyRunHistory` must list saved runs and open report URLs.
- `StrategyDiagnosticsPanel` must expose diagnose, anti-overfit, rolling validation, and optimize actions after a successful strategy task.
- Strategy result display must include:
  - score and grade
  - decision
  - OOS summary
  - data-quality summary
  - latest holdings
  - validation issues
  - risk logs
  - report link
  - candidate export
- `summary_json` must remain a server artifact. The browser must not expose it as a primary user-facing download unless the backend returns a safe route.

### Acceptance Checklist

- A user can instantiate a template, edit common fields in a form, validate, and submit a strategy backtest.
- A user can save the validated spec, reload it, and submit it again.
- A user can save a completed run and find it in run history.
- Diagnose, anti-overfit, rolling validation, and optimize actions render structured results or backend errors.
- The raw JSON editor remains available.
- `cd frontend && npm run build` exits 0.

## Track E - Composite and Factor Comparison Convergence

### Intent

Make composite and comparison pages consistent with the shared task/result patterns and remove duplicated picker/error handling.

### Files to Change

- `frontend/src/components/CompositeBuilder.tsx`
- `frontend/src/components/FactorComparison.tsx`
- `frontend/src/api/composite.ts`
- `frontend/src/api/comparison.ts`
- `frontend/src/App.tsx`

### New Files

- `frontend/src/components/factors/FactorExpressionRows.tsx`
- `frontend/src/components/factors/FactorLibraryPicker.tsx`
- `frontend/src/components/factors/FactorBacktestSettings.tsx`
- `frontend/src/components/common/ErrorNotice.tsx`
- `frontend/src/components/common/LoadingButton.tsx`

### Required Behavior

- Replace duplicate factor-library picker implementations in `CompositeBuilder` and `FactorComparison` with `FactorLibraryPicker`.
- Replace `alert()` error paths with `ErrorNotice` in:
  - composite submit
  - factor picker load
  - factor comparison submit
  - attribution fetch
- `FactorBacktestSettings` must expose common settings used by composite/comparison:
  - universe
  - start_date
  - end_date
  - n_groups where supported
  - holding_period where supported
  - benchmark where supported
- Composite task submission must enter the shared task workflow and use `TaskDetailDrawer` when complete.
- Factor comparison can remain synchronous but must display per-factor failures in-page.

### Acceptance Checklist

- Composite submit no longer relies on `alert()` for expected backend errors.
- Factor comparison no longer relies on `alert()` for expected backend errors.
- Both pages use the same factor picker component.
- Composite result can be inspected through the unified task detail UI.
- `cd frontend && npm run build` exits 0.

## Final Hard-Stop Verification

Run these commands from the repository root after all five tracks are implemented:

```bash
cd frontend && npm run build
```

Run backend route regressions if any frontend work required backend contract changes:

```bash
.venv/bin/python -m pytest tests/test_routes_backtest.py tests/test_wq_brain.py tests/test_wq_brain_batch.py tests/test_routes_strategy.py -q
```

Run static whitespace validation:

```bash
git diff --check
```

## Completion Exit Gate

The frontend completion work is done only when all conditions below are true:

- The WQ BRAIN console exists as its own frontend tab and supports simulation, guarded formal submit, direct submit, batch submit, alpha status, and finalize.
- Single-factor backtest UI can submit OOS, data-quality, direction, universe-date, and rebalance-anchor options.
- Strategy Workbench supports structured template editing, spec save/load, run history, diagnostics, anti-overfit, rolling validation, optimize, report, and export.
- Composite and comparison pages share factor picker and error-display components.
- All asynchronous task types use the shared task status/result workflow.
- No user-facing WQ credential input exists.
- WQ BRAIN is not represented as a StrategySpec adapter.
- `cd frontend && npm run build` exits 0.
- If backend contracts changed, the listed pytest route regressions exit 0.
- `git diff --check` exits 0.

When every item above is satisfied, stop implementation and report:

```text
Frontend completion plan reached the exit gate.
Verified: frontend build, route regressions if applicable, and diff whitespace check.
Remaining scope: none inside docs/frontend_completion_plan.md.
```
