# Agent Safe Workflow

## 主代理准备项目与轮次

使用 `create_research_project` 或 `list_research_projects` 确认项目。真实数据来自 `list_research_snapshot_imports` 列出的共享清单，按 ID 用 `register_research_snapshot` 导入。用 `get_research_preparation` 检查字段、股票数量和日期，将返回的 `data_input` 写入选择期配置。合成演练可通过 `prepare_research_demo` 取得面板、示例定义和策略模板。

在第一次评价前用 `register_research_holdout` 固定最终窗口。若需量化增量，在选择期用 `evaluate_factors` 与 `run_research_strategy` 保存基线，再向 `create_research_round` 传入 `baseline_run_id`。为每个子代理登记不同的 `name` 和 `hypothesis`，保存主代理的 `coordinator_key`，向各子代理分别提供 `project_id`、`round_id`、对应的 `direction_id` 和 `direction_key`。

## 子代理同时探索各自方向

1. 从快照可用字段构造候选表达式，先调用 `check_research_factor_novelty`。根据 `exact_matches` 找到正在开发或已保存的公式，根据 `family_matches` 检查参数变体与当前假设是否有独立研究价值。挖新因子时向 `submit_research_direction` 传 `only_new=true`，若返回 `known_candidates`，从 `new_indices` 挑选新表达式重新提交。刻意复验已有公式时使用默认提交模式；同一评价可以被多个方向引用。
2. 通过 `get_mcp_task_status(task_id, include_result=true)` 获取状态与结果。从成功评价取得 `evaluation_id`、`evaluation_hash`、`definition_hash`、`project_id` 和 `backend`，用冻结配置的 `window.phase` 填写组件引用的 `scope`。后续改进可带 `parent_evaluation_id`。可用 `import_research_iteration` 将已有迭代中实际观察过的试验登记到方向历史。
3. 调用 `finish_research_direction` 表明已结束该方向的提交。遇到失败或取消时记录任务状态和原因，已观察的成功、失败筛选结果均应计入选择过程。

## 主代理合并并终验

主代理用 `get_research_round` 检查各方向任务，用 `submit_round_strategy` 提交带组件引用的 v2 组合。`compare_research_strategies_to_baseline` 对比预登记基线与增强组合的配对净收益、换手和信号相关性。若要探索组合风险参数，在冻结入选组合前运行 `optimize_research_portfolio`。最终任务冻结时一并封存已观察的参数试验。

当各方向结束且相关任务终止后，用 `finalize_research_round` 提交唯一 `selection_run_id`。任务结果包含冻结的 `trial_manifest`、`validation` 和 `validation_artifact`。按 `get_research_artifact` 读取报告，按 `export_research_strategy` 的实际决策处理候选信号导出。保留 `external_data_acceptance` 状态，复核真实市场数据覆盖。[完整接口](RESEARCH_API.md) · [研究执行状态](US_RESEARCH_EXECUTION_STATUS.md)

## Historical diagnostic tools

The following single-expression tools also support asynchronous requests:

- `run_backtest`
- `score_factor`
- `compute_factor_values`
- `run_anti_overfit`
- `run_rolling_validation`

Use `cancel_mcp_task(task_id)` when the current direction should stop. Cancellation is cooperative: a task may
finish the current stock, batch, or remote data call before it reports `cancelled`.

Single-stock research flow:

```text
get_stock_history(stock_code, start_date, end_date)
check_market_cache(universe, start_date, end_date, stock_code)
```

Do not treat a single-stock question as permission to run full-universe
`compute_factor_values(csi500)` or `score_factor(csi500)`.

Result fields to preserve:

- `experiment_id`
- `factor_hash`
- `config_hash`
- `data_snapshot_id`
- `validation_provenance`
- `promotion_blockers`

Common stop conditions:

- biased direction mode
- missing OOS summary
- missing data-quality proof
- missing snapshot proof
- failed multiple-testing check
- duplicate-factor similarity
- `STOCK_CACHE_MISSING`
- `REMOTE_PREFETCH_REQUIRED`

Safe usage note: do not reinterpret `research_only` payloads as authorization
for candidate/export/submit boundaries.

Cancellation note: a user- or agent-cancelled task is not itself a factor failure and should not be recorded as a
reject/export blocker unless the completed metrics or error payload justify that conclusion.
