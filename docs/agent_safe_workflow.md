# 多代理共享因子研究操作指南

主代理和子代理在同一项目内开发因子时，按本指南从行情登记走到组合终验。从默认分支 `main` 启动 MCP 后，用 `tools/list` 确认 `create_research_round`、`submit_research_direction`、`finalize_research_round` 已注册。遇到回执丢失、任务停滞或空回包，按[MCP 调用超时与恢复](#mcp-调用超时与恢复)处理。连接配置见 [MCP 指南](MCP_GUIDE.md)，参数和返回值见 [研究 API](RESEARCH_API.md)。

## 1. 主代理准备项目和身份

用 `create_research_project` 创建项目，或从 `list_research_projects` 选择已有项目。开轮需要项目 `owner` 或 `admin` 角色。一个项目可以登记一个受管轮次。`get_research_round(project_id)` 只返回项目与轮次 ID，取得 `round_id` 后传入这两个 ID 可查看完整轮次。

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
4. 用 `create_research_round` 传入项目 ID、`round_setup` 的 `config`、`holdout_start`、`holdout_end` 和 `directions`。有基线时另传 `baseline_run_id`。为能在回执丢失后恢复，主代理应在首次开轮前生成并保存至少 32 字符的随机 `coordinator_key` 和原请求。收到回执后，向每个子代理交付 `project_id`、`round_id` 及其自己的 `direction_id`、`direction_key`。主代理保管 `coordinator_key`。

## 3. 子代理按方向并行研究

1. 根据分配的假设和快照字段编写候选。需要预登记具体公式的经济假设时，在该公式首次评价前调用 `register_research_hypothesis`。
2. 调用 `check_research_factor_novelty(project_id, expressions)`。逐项检查 `candidates[i].exact_matches` 的来源及 ID，查看 `family_matches` 判断改动参数是否有独立研究价值。预查为只读操作，其他方向仍可能先提交。
3. 通过 `submit_research_direction` 提交方向凭据与 `definitions`，每项携带表达式和用到的字段契约。首次提交前生成并保存该请求的 `idempotency_key` 和完整参数。开发新因子时传 `only_new=true`，入队事务会再次查重。返回 `status=known_candidates` 表示整批未入队，根据 `new_indices` 选择原批次中的新项重提。修改批次时使用新的请求键。刻意复验时可使用默认模式复用相同计算。
4. 拿到 `task_id` 后，用 `get_mcp_task_status(task_id, include_result=true)` 轮询。保留候选的 `evaluation_id`、`evaluation_hash`、`definition_hash`、`project_id`、`backend` 和冻结窗口的 `phase`，供主代理构造组合引用。改进已有候选时可传本轮的 `parent_evaluation_id`。需计入已开展的旧迭代时，用 `import_research_iteration` 登记其 `task_id`。
5. 停止某个轮次任务时，用 `cancel_research_round_task` 传方向凭据和该任务 ID。完成该方向全部提交后用 `finish_research_direction` 关闭方向。在途任务仍须等待终态。子代理离线时主代理可用协调者密钥接手取消任务或关闭方向。

## 4. 主代理汇总试验并终验组合

- **选择和计数。** 用 `get_research_round` 读取各方向的提案、指标、失败原因及评价引用。开轮期间 `trial_count_scope=live_project`，共用 `observed_trials`。服务以实际观察到表现的评价、组合参数试验和已导入的旧试验按身份去重。多方向引用同一评价可共享计数，取消前已经观察到的结果仍可入账。试验分母以服务端清单为准。
- **比较组合。** 用 `submit_round_strategy` 传协调者凭据及 v2 `spec`，其中 `factor_evaluations` 引用本轮或已登记基线的组件。读取完成任务的 `result.strategy_run_id`。有基线时可用 `compare_research_strategies_to_baseline` 比较同窗口净收益、换手和信号相关性。若探索组合风险参数，先在选择期调用 `optimize_research_portfolio`，再选定组合。
- **冻结终验。** 各方向均已结束、相关任务进入 `completed`、`failed` 或 `cancelled` 且入选组合的结果齐备后，主代理将本轮已完成的选择期组合 ID 作为 `selection_run_id` 传给 `finalize_research_round`。服务冻结入选组合及 `trial_manifest`。此后 `get_research_round` 返回 `trial_count_scope=frozen_round`。终验提交回执丢失时先查 `final_task`。已确认取消的冻结终验可由主代理用原 `selection_run_id` 和原凭据恢复。已失败的终验应记录原错误。
- **留出期冲突。** `LEGACY_HOLDOUT_EVIDENCE_INCOMPLETE` 表示旧来源的日期或证据不足，可补齐档案、重新导入并核查。`LEGACY_HOLDOUT_EXPOSED` 表示已接触该留出期。项目的留出期一经登记便固定，此时应结束当前终验尝试，在新项目中预登记真正独立的窗口，并将影响选择的旧试验纳入记录。

## 5. 读取实际验收和导出决定

用 `get_mcp_task_status(task_id, include_result=true)` 查询已完成任务，按发起任务的工具读取轮询响应。

| 发起工具 | 总体外部行情验收 | 各分项 |
| --- | --- | --- |
| `validate_research_evaluation`，独立验证评价 | `result.external_acceptance.status` | `result.external_acceptance.checks` |
| `finalize_research_round`，轮次终验 | `result.validation.external_acceptance.status` | `result.validation.external_acceptance.checks` |

逐项读取 `prices`、`historical_universe`、`corporate_actions`、`benchmark` 的 `status` 和原因。只在服务明确返回 `not_run` 时这样记录。若预期字段缺失，核对任务类型、终态、`include_result` 和错误信息，再报告缺少哪项结果。快照登记时的 `external_data_acceptance` 只描述登记阶段，独立验证和轮次终验使用上表路径。

轮次终验还返回 `result.trial_manifest`、`result.validation` 和 `result.validation_artifact`。用其中的 `artifact_id` 调用 `get_research_artifact` 可复读报告。需要导出时，以终验任务的 `result.strategy_run_id` 调用 `export_research_strategy`，保留服务端决定及阻断原因。

## MCP 调用超时与恢复

支持 `submit_only=true` 的长操作优先异步提交，收到 `task_id` 就保存。共享轮次的方向、组合和终验提交本身会返回任务回执。每次 MCP 调用都要有客户端截止时间，状态查询也一样。项目桥接脚本的单次请求默认 180 秒，可通过 `QUANTGPT_MCP_REQUEST_TIMEOUT` 或 `--timeout-seconds` 调整。直接连接 MCP 的 Agent 也要在宿主设置读取期限，以便在服务失联时收回控制权。[连接配置](MCP_GUIDE.md)

### 提交后没收到有效回执

超时、断线或空回包时，原写入仍可能成功。记录“提交结果未知”，保留原身份、工具、参数和请求键，再按下列顺序核对。

1. 已有 `task_id` 时先查 `get_mcp_task_status(task_id, include_result=true)`。已知轮次时先读 `get_research_round(project_id, round_id)`，方向任务在对应的 `directions[].tasks`，选择期组合在 `strategy_tasks`，终验在 `final_task`。并行运行时，列表里最后一条也可能属于另一笔提交。
2. 缺少确切的 `task_id` 时，以原身份调用只读的 `find_research_submission`。传 `project_id`、原工具名 `submission_tool`，并按下表标识原请求。每个请求键只对应一份原参数，若键曾用于不同参数，需先核对原任务内容。找到匹配回执后沿用返回的 `task_id`。桥接脚本可能已做过一次只读查询，先查看错误响应中的 `reconciliation`。首次异步提交独立评价或策略时也应预先保存显式 `idempotency_key`，供回执丢失时查询。

   | 原提交工具 | 定位原提交所需的其他参数 |
   | --- | --- |
   | `submit_research_direction` | `round_id`、`direction_id`、原 `idempotency_key` |
   | `submit_round_strategy` | `round_id`、原 `spec` |
   | `finalize_research_round` | `round_id`、原 `selection_run_id` |
   | 异步调用 `evaluate_factors`、`run_research_strategy` | 原显式 `idempotency_key` |

3. 若返回 `found=false`，原提交仍可能在处理中。稍后再做一次只读查询。仍无记录时，仅对支持幂等提交的工具用原身份、原参数、原请求键恢复一次。若开轮回执丢失且没有 `round_id`，先查 `get_research_round(project_id)`。`round_id=null` 表示当前未查到该项目的轮次。需要取回方向凭据或恢复原开轮时，由主代理使用首次保存的 `coordinator_key` 和完整开轮请求。
4. 若查询本身一直失败、缺少原凭据或写入接口没有幂等约定，停止自动重发并将未确认的操作交给主代理核对。恢复期间保留原任务记录，其他方向可继续独立工作。

### 已有任务编号，但任务没有进展

通过 `get_mcp_task_status(task_id, include_result=true)` 做有界轮询。首次看到 `queued` 或 `pending` 时开始计排队时长。看到 `running` 后，记录最近一次阶段、已完成数量或进度实际变化的时间。默认排队或业务无进展满 300 秒转入诊断，整轮最长观察 900 秒。当次查询的期限取单次上限和剩余观察预算中的较短者。后台默认的 600 秒执行期限从领取任务后开始。续租、`updated_at` 和 `revision` 都可能在业务进度静止时更新。

达到停滞阈值后结束本次轮询，在剩余预算内读一次轮次状态和原任务。记录任务编号、状态、最近阶段、停滞时长和错误。排队时检查调度与执行容量，运行中则检查对应阶段或上游数据。连续两次只读查询都未取得有效响应，或已用完整轮预算时，将当前记录交给主代理。恢复连接后从原任务继续。仅在决定取消时调用有凭据的 `cancel_research_round_task`。处于 `interrupted`、`remote_outcome_unknown` 或 `reconciliation_required` 的任务需核对原执行结果。相关方向仍有未决任务时暂缓组合终验。

### 收到响应，但没有可用结果

检查 MCP 错误标记、有效载荷和业务 `error_code`。工具要求返回数据时，无内容、空白、无效格式或缺少必需字段都应记为异常。若是写入回执，按上面的顺序查原任务。合法空列表表示此次查询有零条记录，`insufficient_data` 和 `known_candidates` 则按各自的业务状态处理。

任务标记为 `completed` 时，确认查询带了 `include_result=true`。原查询省略结果时补查一次。结果仍为 `null`、缺少必需评价引用或终验报告时，报告具体缺失项及 `task_id`，停止等待该任务，暂缓依赖该结果的组合或导出。服务明确返回 `failed`、`cancelled` 或其他终态时也结束轮询，记录状态和原因。
