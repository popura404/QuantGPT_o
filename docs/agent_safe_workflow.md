# 多代理共享因子研究操作指南

主代理和子代理在同一项目内开发因子时，按本指南从行情登记走到组合终验。MCP 客户端用 `tools/list` 检查是否已注册 `create_research_round`、`submit_research_direction`、`finalize_research_round`。连接配置见 [MCP 指南](MCP_GUIDE.md)，参数和返回值见 [研究 API](RESEARCH_API.md)。

## 1. 主代理准备项目和身份

用 `create_research_project` 创建项目，或从 `list_research_projects` 选择已有项目。开轮需要项目 `owner` 或 `admin` 角色。一个项目可以登记一个受管轮次，已有轮次可用保存的 `round_id` 调用 `get_research_round` 查看。

| 代理如何连接 | 主代理开轮前的操作 |
| --- | --- |
| 多个连接使用同一个 MCP 身份 | 每个方向填写独立的 `name`、`hypothesis`。可以省略 `actor_id`，方向归开轮身份，各连接分别领取自己的方向密钥。 |
| 子代理使用独立账号 | 先由项目所有者或管理员调用 `PUT /api/v1/research/projects/{project_id}/members`，请求体包含子代理的 `user_id` 和 `"role": "researcher"`。开轮时将同一个用户 UUID 填入该方向的 `actor_id`。子代理用自己的身份连接，服务会同时校验项目写权限、账号和方向密钥。 |

## 2. 主代理登记数据、固定窗口并开轮

1. 用 `list_research_snapshot_imports` 发现部署方发布的真实行情，用 `register_research_snapshot` 登记清单。保存原样返回的 `data_input`。调用 `get_research_preparation` 核对字段、证券数、日期覆盖和已有留出期。合成演练可改用 `prepare_research_demo` 返回的显式短窗 `round_setup`。
2. 对真实日频行情，用 `plan_research_windows(project_id, config)` 只读规划。下面用 Python 表示传给 MCP 的参数，其中 `registered_snapshot` 是登记响应，`scope` 来自该面板的研究设计，含市场、币种、股票池 ID/模式/版本、日历 ID/版本、时区和基准。示例按每 5 个交易日调仓，费用和滑点按本次设计填写。

   ```python
   plan_args = {
       "project_id": project_id,
       "config": {
           "backend": "local",
           "scope": scope,
           "direction": "higher_is_better",
           "data_inputs": [registered_snapshot["data_input"]],
           "window": {"phase": "selection", "label_horizon_sessions": 5},
           "simulation_config": {
               "currency": scope["currency"], "evaluation_start_state": "fresh_cash",
               "rebalance_every_sessions": 5, "fees_bps": fees_bps, "slippage_bps": slippage_bps,
           },
       },
   }
   ```

   默认按交易日依次预留至少 30 日预热、756 日选择、`max(5, label_horizon_sessions, purge_sessions)` 日隔离和 252 日最终留出。预热声明更长时取声明值。按示例的标签周期和默认预热，至少需要 1,043 个交易日。`status=insufficient_data` 时查看 `required_sessions`、`available_sessions`、`missing_sessions`，若报告 `missing_calendar_sessions` 则按列出的日期核对快照。`status=ready` 时保存返回的 `round_setup`。自定义研究窗口需同时明确选择期和留出期的四个起止日。
3. 若要比较新增因子带来的增量，在项目首次评价前用 `register_research_holdout(project_id, start, end)` 登记 `round_setup` 的留出日期。在完全相同的选择配置上用 `evaluate_factors` 和 `run_research_strategy` 取得已完成基线的 `strategy_run_id`。无基线时可直接开轮，由开轮服务登记留出。
4. 用 `create_research_round` 传入项目 ID、`round_setup` 的 `config`、`holdout_start`、`holdout_end` 和 `directions`。有基线时另传 `baseline_run_id`。为可重试的创建请求，主代理可在首次提交前生成并保存至少 32 字符的随机 `coordinator_key`，超时后用同一组配置、方向、身份和密钥重试。保存成功响应。向每个子代理交付 `project_id`、`round_id` 及其自己的 `direction_id`、`direction_key`。主代理保管 `coordinator_key`。

## 3. 子代理按方向并行研究

1. 根据分配的假设和快照字段编写候选。需要预登记具体公式的经济假设时，在该公式首次评价前调用 `register_research_hypothesis`。
2. 调用 `check_research_factor_novelty(project_id, expressions)`。逐项检查 `candidates[i].exact_matches` 的来源及 ID，查看 `family_matches` 判断改动参数是否有独立研究价值。预查为只读操作，其他方向仍可能先提交。
3. 通过 `submit_research_direction` 提交方向凭据与 `definitions`，每项携带表达式和用到的字段契约。开发新因子时传 `only_new=true`，入队事务会再次查重。返回 `status=known_candidates` 表示整批未入队，根据 `new_indices` 选择原批次中的新项重提。对同一请求的网络重试沿用原参数和 `idempotency_key`，修改批次时使用新的请求键。刻意复验时可使用默认模式复用相同计算。
4. 拿到 `task_id` 后，用 `get_mcp_task_status(task_id, include_result=true)` 轮询。保留候选的 `evaluation_id`、`evaluation_hash`、`definition_hash`、`project_id`、`backend` 和冻结窗口的 `phase`，供主代理构造组合引用。改进已有候选时可传本轮的 `parent_evaluation_id`。需计入已开展的旧迭代时，用 `import_research_iteration` 登记其 `task_id`。
5. 停止某个轮次任务时，用 `cancel_research_round_task` 传方向凭据和该任务 ID。完成该方向全部提交后用 `finish_research_direction` 关闭方向。在途任务仍须等待终态。子代理离线时主代理可用协调者密钥接手取消任务或关闭方向。

## 4. 主代理汇总试验并终验组合

- **选择和计数。** 用 `get_research_round` 读取各方向的提案、指标、失败原因及评价引用。开轮期间 `trial_count_scope=live_project`，共用 `observed_trials`。服务以实际观察到表现的评价、组合参数试验和已导入的旧试验按身份去重。多方向引用同一评价可共享计数，取消前已经观察到的结果仍可入账。试验分母以服务端清单为准。
- **比较组合。** 用 `submit_round_strategy` 传协调者凭据及 v2 `spec`，其中 `factor_evaluations` 引用本轮或已登记基线的组件。读取完成任务的 `result.strategy_run_id`。有基线时可用 `compare_research_strategies_to_baseline` 比较同窗口净收益、换手和信号相关性。若探索组合风险参数，先在选择期调用 `optimize_research_portfolio`，再选定组合。
- **冻结终验。** 各方向均已结束，相关任务进入 `completed`、`failed` 或 `cancelled` 后，主代理将本轮已完成的选择期组合 ID 作为 `selection_run_id` 传给 `finalize_research_round`。服务冻结入选组合及 `trial_manifest`。此后 `get_research_round` 返回 `trial_count_scope=frozen_round`。遇到中断或不确定状态，先处理原任务再终验。已冻结的终验请求用原 `selection_run_id` 重试，取消后的终验可由主代理用原请求恢复。
- **留出期冲突。** `LEGACY_HOLDOUT_EVIDENCE_INCOMPLETE` 表示旧来源的日期或证据不足，可补齐档案、重新导入并核查。`LEGACY_HOLDOUT_EXPOSED` 表示已接触该留出期。项目的留出期一经登记便固定，此时应结束当前终验尝试，在新项目中预登记真正独立的窗口，并将影响选择的旧试验纳入记录。

## 5. 读取实际验收和导出决定

用 `get_mcp_task_status(task_id, include_result=true)` 查询已完成任务，按发起任务的工具读取轮询响应。

| 发起工具 | 总体外部行情验收 | 各分项 |
| --- | --- | --- |
| `validate_research_evaluation`，独立验证评价 | `result.external_acceptance.status` | `result.external_acceptance.checks` |
| `finalize_research_round`，轮次终验 | `result.validation.external_acceptance.status` | `result.validation.external_acceptance.checks` |

逐项读取 `prices`、`historical_universe`、`corporate_actions`、`benchmark` 的 `status` 和原因。只在服务明确返回 `not_run` 时这样记录。若预期字段缺失，核对任务类型、终态、`include_result` 和错误信息，再报告缺少哪项结果。快照登记时的 `external_data_acceptance` 只描述登记阶段，独立验证和轮次终验使用上表路径。

轮次终验还返回 `result.trial_manifest`、`result.validation` 和 `result.validation_artifact`。用其中的 `artifact_id` 调用 `get_research_artifact` 可复读报告。需要导出时，以终验任务的 `result.strategy_run_id` 调用 `export_research_strategy`，保留服务端决定及阻断原因。
