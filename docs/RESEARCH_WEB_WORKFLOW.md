# Research web workflow

The web UI uses the same authenticated project services as MCP. The global project selector is membership-scoped; the personal legacy library remains available under “个人旧收藏”. Creating a project does not mark any result validated.

## Implemented flow

1. Select or create a project.
2. Open “项目研究” and prepare the explicitly labelled **synthetic offline fixture**. The server freezes generated data and returns its actual manifest/content identity, evaluation configuration and a strategy template.
3. Edit the expression and evaluate it. Declared field contracts are copied from the server field catalog for known identifiers; unknown fields remain subject to the server parser and capability checks. An optional economic hypothesis is registered before evaluation and cannot overwrite prior observed research. The result displays server status, finite metrics, evidence status and blockers. Missing data capabilities produce a visible rejected evaluation with its failure reason, rather than an empty successful result.
4. Save the evaluation into “共同因子池”. The entry carries the server evaluation ID. Project favorites are shared tags; deleting a shared entry asks for confirmation and affects all project members. Pool detail resolves the evaluation through the authenticated endpoint.
5. Run a v2 strategy using those evaluation references. The browser builds `factor_evaluations` from server responses; the server verifies lineage. Read the return artifact on demand to display the net-value curve, including initial cash as 1.
6. Request research export by project/run ID. The browser supplies no caller validation evidence. Missing final-test or validation-profile evidence is displayed as a blocked export with readable reasons; raw details are optional.
7. Use the saved server signal reference for portfolio optimization, with editable single-asset and cash caps. The response shows feasibility, target weights, cash, blocked constraints or an explicit baseline fallback. This is a separate research allocation; it does not create export permission.

Reload restores remembered evaluation/run IDs and reads them again from the server. It does not treat local storage as authoritative evidence. Logout clears the selected project. A different account cannot read the original project's run.

## Existing strategy workbench

The existing v0/v1 workbench now has editable dates, market-aware benchmarks and fields, mutually exclusive top-N/quantile selection, fees in bps, readable metrics/holdings, and an optional JSON editor. Task IDs are remembered per selected project for status recovery. General task statuses distinguish interruption, unknown remote outcomes and local waiting cancellation. Cancellation displays the confirmed server state instead of fabricating success.

The weight-normalization preview requires actual dated `score` rows. Target portfolio weights are not treated as scores. Legacy results without a server project/run reference cannot be exported via the research export API.

Legacy `/strategy/specs` and `/strategy/runs` persistence only exposes personal records. A project association on either the strategy or the task requires the research routes and current project membership, including for the original author after membership revocation. Caller-written legacy runs cannot be attached to a project strategy or task. Research run reads, idempotent reuse and export verify the frozen configuration/hash, stored v2 spec and server experiment-to-run links; older records without these links fail closed and require a fresh evaluation under the current engine identity.

## Verification

Use the repository Python 3.12 environment and installed Microsoft Edge on Windows:

```powershell
npm.cmd --prefix frontend run build
cd frontend
npm.cmd run test:e2e
```

`frontend/e2e/server.py` starts the real API application with an isolated SQLite database, real password/JWT authentication and project/evaluation/strategy/artifact services. Its test lifespan omits background schedulers, mail and external market requests. It calls the actual MCP save function with an authenticated test principal to seed the shared pool. This verifies shared application services, not MCP network transport. The offline data source is explicitly synthetic; HTTP routes are not browser-mocked.

Playwright uses `frontend/playwright.config.ts`. Outputs are ignored under `test-results/`: JSON results, isolated databases/snapshots, failure traces and a full-page research workflow screenshot. The tests cover MCP-created entries visible in the web UI; project favorites; expired access-token refresh; expression field-contract synchronization and hypothesis registration; evaluation, pool save, v2 strategy, returns artifact and blocked export; feasible and infeasible saved-signal optimization; refresh/relogin recovery; another user's 404 response; project creation; editable dates and market capabilities; and a missing-field blocker.

The final 2026-10-05 local run, after the project-permission/origin fixes and numerical-dependency identity freeze, completed **3 tests in 29.7 seconds** using installed Edge. The production TypeScript/Vite build passed before these backend-only fixes. The regenerated full-page `test-results/research-workflow.png` was inspected: the complete return curve, readable export blockers and infeasible saved-signal constraints are visible.

The focused backend route/persistence/workflow suite passed **17 tests in 18.61 seconds**. After the final runtime-identity change, the two files below passed **5 tests in 25.39 seconds**, including both real computation workflows. These are overlapping regressions, not 22 distinct tests. They cover revoked-author legacy reads/writes, project-task-only associations, valid shared viewer reads, idempotent reuse, 13 stored-record tampering cases rejected by both read/export, and stale worker-attempt isolation. Ruff passed for the changed route/research/test files, and Pyright reported zero errors for the two changed backend modules.

```powershell
.venv312/Scripts/python.exe -m pytest tests/test_strategy_project_isolation.py tests/test_research_workflow.py -q --basetemp=test-results/p01-security-frozen -o cache_dir=test-results/p01-security-cache
```

Rerun these commands when subsequent backend changes affect the workflow; source or numerical dependency changes intentionally change evaluation identity.

## Remaining limits

- This is a verified local application flow with synthetic inputs. It is not actual US market integration evidence or evidence of a useful investment strategy.
- Preparing arbitrary real-provider evaluation configurations, multi-factor construction and holdout preregistration still require the existing API/MCP contracts. The guided UI currently starts with the supplied single-factor offline recipe.
- The project pool initially shows the first 200 entries and labels that limit; pagination is not implemented.
- General task interruption/remote-state labels are implemented, but browser fault injection for lease expiry, process restart, SSE disconnect and remote reconciliation is not included in this suite. Backend tests cover separate task-service invariants.
- Offline evaluation/run requests use the currently provided request/response research endpoints; the UI does not claim these requests can be cancelled as durable jobs.
- Production bundle size still triggers Vite's existing chunk-size warning; that is separate from correctness of the research workflow.
