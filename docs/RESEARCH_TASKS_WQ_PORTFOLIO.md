# 持久任务、WQ 平台研究与组合优化契约

本文记录 P11、P12、P16 的实现和离线验证边界，补充 [研究 API](RESEARCH_API.md)、[执行状态](US_RESEARCH_EXECUTION_STATUS.md) 与 [执行计划](US_RESEARCH_EXECUTION_PLAN.md)。不代表 G2/G3/G4 整体通过，也不代表真实 WQ 模拟、真实美股完整风险数据或研究候选已通过独立终验。

## 1. P11：接受、派发与不可变请求

核心入口为 `task_store.submit_durable_task`、`task_executor.dispatch_durable_task`、`research.jobs.submit_research_request`。新研究任务为 `task_type=research_evaluation`，完整请求保存在 `params`，`kind` 为 `factor` 或 `strategy`。

1. 校验请求字段；未知顶层字段明确拒绝。因子定义、评价配置及策略规格使用版本化模型验证。
2. 服务端补齐实际有效配置。本地引擎身份来自运行版本和源文件内容；WQ 补齐固定及可配置远程 settings，不能由调用者声明已验证引擎或底层数据版本。
3. 冻结 actor、project、请求参数与请求哈希，写入 `server_config_frozen=true`。任务行与 `dispatch_pending=true` 同事务提交，提交成功后才返回接受结果。
4. 派发通过条件更新竞争领取，核对 queued/pending、revision、取消标记与 outbox 标记；成功时生成新的 `attempt_id`，状态变为 running 并取得租约。
5. worker 接收与缓存独立的深拷贝。新研究任务执行前比较冻结的本地引擎身份和当前 worker 身份；部署变化返回 `CODE_VERSION_CHANGED`，不静默替换哈希后执行。

幂等键按 actor、project、task_type 隔离。同键、同有效请求重用原任务；同键不同请求返回 `IDEMPOTENCY_CONFLICT`。不提供幂等键时允许创建新任务。因为服务端有效引擎身份也是输入，部署后相同原始文本请求可能与旧幂等键冲突；已接受任务仍保留原配置。

任务幂等与研究内容身份分开：多个任务可能指向同一不可变评价；基础设施恢复不应自动增加已观察的研究试验数。该机制不承诺外部平台 exactly-once，也不承诺任意旧入口均可自动重放。派发器只重放明确注册的本地处理器。

## 2. 状态、取消、重启与发布

| 场景 | 状态/行为 | 自动重发边界 |
|---|---|---|
| 已落库，尚未领取 | `queued`，`dispatch_pending=true` | 重启后可重新派发 |
| 当前 worker 有租约 | `running`；旧入口还保留计算阶段名称 | 定期持久化心跳与进度 |
| 正常完成 | `completed` | 终态不能由迟到 worker 改写 |
| 本地执行错误 | `failed` | 不默认重跑研究 |
| 本地取消 | `cancelled`，取消标记为真 | 不能再由 worker 写成完成 |
| 本地失去租约 | `interrupted`，`retryable=true` | 通过显式 `retry_durable_task` 重新排队并生成新 attempt |
| 远程执行失联 | `remote_outcome_unknown`，`retryable=false` | 禁止自动重新 POST |
| 无可靠远程引用 | `reconciliation_required` | 需要人工或已验证的平台查询能力 |
| 只停止本地等候 | `local_wait_cancelled` | 不表示远程取消成功；后续可显式对账 |
| 平台明确返回取消状态 | `remote_cancel_confirmed` | 才能宣称远端已取消 |

`interrupted`、远程未知和本地停止等候属于普通 worker 不得覆盖的终态；显式恢复/对账服务是受控例外。旧 `iteration_completed` 状态保留兼容。通用本地 retry 目前是服务函数，不能据此假定存在额外 HTTP retry 路由；公开端点以 [API 文档](RESEARCH_API.md) 和实际路由为准。

取消为合作式。缓存、provider 等待及计算边界检查取消；已开始的阻塞调用不能被 Python 线程强制中断。任务超时、HTTP 连接/读取超时与轮询次数均有上限；远程超时保留未知结果，不把本地线程停止当作平台取消。

worker 发布使用两层保护：

- 内存层：`transition_task(expected_attempt=...)` 在 `tasks_lock` 内比较 attempt 并更新；`snapshot_task` 在同一锁内取得对应 attempt 的深拷贝。旧 worker 的成功、错误、取消与超时分支都不能修改新 attempt 的缓存。
- 数据库层：持久化核对 actor、当前项目权限、attempt、revision、有效租约及非终态/未取消条件。即使重试尚停在 queued、数据库 attempt 已清空，旧的非空 attempt 仍被拒绝。研究 artifact 发布前通过条件 UPDATE 锁定任务行，锁持有到产物事务提交，使取消与产物发布按提交顺序决定胜者。

2026-10-05 补充的缓存竞态修复针对一个不能仅靠数据库保护解决的缺陷：旧 worker 在数据库被拒绝前，曾可能先把新 attempt 缓存标记为取消或完成。新增真实线程/SQLite 回归覆盖迟到成功、迟到取消异常、claim 快照不受缓存改变影响，以及旧 attempt 不得覆盖重试 queued 状态。

SSE 使用 revision 作为事件 ID，同阶段进度变化也发送；携带 `Last-Event-ID` 重连时返回当前快照并继续订阅。当前没有历史事件流保留或完整事件重放承诺。内存完成结果在其 revision 持久化前不作为已发布结果展示。

## 3. 项目权限与撤权

actor 来自认证上下文或服务端 MCP 连接配置，不能用传入 project_id 冒充身份。项目任务、结果、artifact、缓存读取和后台发布均重新核对当前成员关系；viewer 不能执行写操作。项目外对象返回不可访问，不通过缓存绕过数据库授权。

`record_remote_task_reference` 是一个窄化例外：已发送请求的同一 worker attempt 可以在本地取消后保存刚到达的远程引用，避免丢失外部副作用线索；它不恢复运行、不发布完成结果，不能用不同 attempt 覆盖已有远程身份。

MCP 状态入口使用异步 `get_mcp_task_status_payload_async`，缓存丢失时读取持久状态。MCP 完成需要原 worker context 的 attempt，不能凭 task_id 创建一项新任务或接管新 attempt。stdio 系统身份须显式加入项目。

## 4. P12：一次提交、证据归档与未知结果对账

`research.wq_backend.evaluate_wq` 在 POST 前保存不可变远程请求 artifact 和请求指纹，并提交 dispatching 状态。`WQBrainClient.start_simulation` 每次调用只发送一次模拟 POST；传输层也关闭 POST 自动重试。

远程参数包括 expression、region、universe、delay、decay、neutralization、truncation，以及明确冻结的语言、instrumentType 等固定 settings。成功接收后先保存 `remote_run_ref`/simulation ID，再开始轮询。远程 Location 限定为预期 HTTPS API 的 simulation 路径。

| 响应/中断 | 处理 |
|---|---|
| 认证失败或明确平台拒绝 | 返回失败原因；不会把请求记作成功 |
| 429 | 明确的已知拒绝；记录限流/可重试信息，但不在相同模拟调用内盲重发 |
| POST 超时、连接丢失、5xx 或不明确重定向 | `remote_outcome_unknown`；请求可能已经被接受 |
| 接受响应缺少安全可用引用 | 保留未知结果，不能重新推断“未提交” |
| 已有引用、GET 轮询超时 | 保留引用；后续恢复只 GET |
| 平台明确 FAILED/ERROR | 记录平台失败及原始响应 |
| 本地取消时 POST 刚返回引用 | 保留引用，状态仍为本地停止等候 |

`reconcile_wq_evaluation` 先检查项目权限和冻结配置。已有引用时只轮询；没有引用且未验证请求指纹查询/平台幂等能力时，返回 `reconciliation_required` 和人工对账动作，不自动重新 POST。普通重复 `evaluate_wq` 返回已保存状态，不创建新的模拟。评价对账结果与原任务历史状态是不同记录，不能假定每次对账会自动改写原 task 的终态。

`remote_evidence` 保存原始模拟响应、可得的 alpha 响应、IS/OOS 指标、远程 ID、观测时间和请求窗口。请求窗口不是平台确实执行该窗口的证明；`window_control_verified=false`。底层数据版本记录 unknown，不伪造本地 manifest；字段与算子映射标记未验证或远程专属。

有服务端保存的请求、引用、原始响应、平台检查和一致平台 scope 时，证据可按 `wq_remote` profile 评价。同一证据不能通过 `local_strategy` profile，也不能变成本地独立 final 证明。现有正式提交 preflight、权限及 override 审计门禁保留。`submit_alpha` 同样不在失去响应后盲重发，不把不明平台状态当作提交成功。

当前 `external_integration=not_run`：测试全部使用 mock，不运行真实模拟或正式提交。远程取消能力、平台底层版本、按指纹查回请求和自定义日期控制仍为 unknown/unavailable。

## 5. P16：保存信号驱动的研究组合

公共入口为 `optimize_signal_reference`。输入是服务端保存的 signal artifact 引用及 `PortfolioOptimizationConfig`，不是调用者自填的大数组或通过证明。读取时校验当前成员权限、内容哈希、项目、来源评价身份、窗口与本地 backend；底层快照还必须在该项目登记。

纯计算入口 `optimize_portfolio` 的当前约定：

- 信号 eligibility 确定候选证券，同一证券重复行不重复分配权重；非候选的已有持仓仍计入卖出需求。
- 预测采用截至 asof_session 的历史收益均值；协方差采用共同有效历史样本并向对角矩阵收缩。未来行不参与估计，收益缺口不前向填充。
- 先用线性规划检查可行性，再求受约束等权投影基线和均值—方差—显式交易成本目标。求解失败可回退已记录的可行基线；等权投影本身失败时明确记录可行顶点基线。
- 保持 long-only、无杠杆，现金为 1 减股票总权重。目标不是保证优于基线。

| 约束 | 口径与缺失处理 |
|---|---|
| 个股权重/现金 | 显式上下限；不可行不静默放松 |
| 行业上限 | 使用截止会话的真实历史行业；缺行业则阻断，不拉取今天分类补过去 |
| size | 对真实正值 market_cap 取自然对数；不使用价格×成交量代理 |
| beta | 请求该约束时必须有真实可用 beta 列；当前不伪造或默认置零 |
| ADV 参与率 | 20 个完整历史会话的 raw_close×真实股数成交量，按 portfolio_nav 转换成允许交易权重；缺数据则阻断 |
| 换手 | 股票买卖绝对名义金额之和/NAV；初始建仓也计入 |
| 成本 | transaction_cost_bps 对双边绝对交易量收费；同时展示基线与配置费用敏感性 |

优化器的换手定义为双边绝对交易量/NAV，和旧报告中可能使用的“一半双边交易量”不同，比较时必须读 `turnover_convention`。所有行业/beta/size 暴露按总 NAV 定义，现金暴露为零。

结果包含 feasibility、逐约束剩余量、最大违反量、估计区间、协方差收缩参数、目标值、基线及 fallback 记录。缺真实风险数据返回具体 blocker。旧 `strategy.optimizer` 保留兼容，但明确标记 `legacy_score_normalization` 和 `risk_optimization_verified=false`。

## 6. 参数选择、研究预算及可解释限制

固定参数模式返回 `parameter_selection=frozen_parameters`，不补造 OOS 增量。可选风险厌恶参数集合要求 `training_end < validation_end < asof_session`：只在训练截止点估计参数，在之后的验证窗口通过共享 raw 价格账本、次会话开盘成交与初始费用比较，选择后再按 asof 历史估计最终分配。

验证结果按同一窗口对比基线，并报告费用敏感性；它被用于选择，因此明确 `validation_is_independent_final=false`。final 评价禁止运行参数选择。参数选择当前从 fresh cash 开始；给出已有持仓时明确返回未支持，而非偷偷假设清仓或携带状态。

所有参数尝试及失败保存在优化 artifact；已观察的风险参数×费用配置计入 `performance_observed_trials`。`observed_trial_count` 纳入这些记录，并按 `portfolio_hash` 去重；相同优化重复读取不会再次构建面板或新增试验数。预登记基线的比较结果单独展示。

输出仍为 `research_only`，`local_strategy_eligible=false`。估计效用不是实现收益，验证期增量不是独立终验。真实历史行业、beta、股本、市值、公司行动、存活/退市资格与可验证风险模型未因合成测试而变成可用。

## 7. 已执行证据与复跑方式

以下是具体检查点计数，集合有交叉，不能相加当作独立测试总数；后续新增用例会改变复跑计数。统一使用 Python 3.12，在仓库根目录执行，临时目录位于已忽略的 data 下。

| 检查点 | 当时结果 | 证明范围 |
|---|---|---|
| P02/P06 算子、neutralize、bridge、OOS 基础回归 | 132 passed | 数值/身份/lookback 与可信 Python 回退；不是 Rust 全算子差分认证 |
| job 冻结、WQ、组合、dispatcher、持久任务集合 | 52 passed | 冻结配置、未知远程结果、权限、优化约束；发生在追加缓存竞态回归前 |
| 组合/WQ 新契约及旧 WQ/preflight 回归 | 75 passed | 新旧 WQ 兼容、平台 scope、优化预算等；全部远程交互为 mock |
| 缓存 attempt 竞态修复后的任务/HTTP/MCP/WQ 集合 | 106 passed | 包含迟到成功、迟到取消异常、独立 claim 快照和 queued 期间旧发布拒绝；修改模块 Ruff/Pyright 均为零诊断 |

```powershell
.venv312/Scripts/python.exe -m pytest tests/test_operator_correctness.py tests/test_expression_parser.py tests/test_neutralize.py tests/test_rust_bridge.py tests/test_oos_validation.py -q --disable-warnings -p no:cacheprovider --basetemp data/test-tmp-p02-final

.venv312/Scripts/python.exe -m pytest tests/test_research_job_freeze.py tests/test_wq_research.py tests/test_research_portfolio.py tests/test_task_executor.py tests/test_durable_tasks.py -q --disable-warnings -p no:cacheprovider --basetemp data/test-tmp-p02-frozen-final --tb=line

.venv312/Scripts/python.exe -m pytest tests/test_research_portfolio.py tests/test_wq_research.py tests/test_wq_brain.py tests/test_wq_submission_guard.py -q --disable-warnings -p no:cacheprovider --basetemp data/test-tmp-p16-budget2 --tb=line
```

缓存竞态修复后的扩大检查集合：

```powershell
.venv312/Scripts/python.exe -m pytest tests/test_durable_tasks.py tests/test_task_store.py tests/test_task_executor.py tests/test_mcp_task_cancel_progress.py tests/test_routes_backtest.py tests/test_routes_strategy.py tests/test_research_job_freeze.py tests/test_wq_research.py -q --disable-warnings -p no:cacheprovider --basetemp data/test-tmp-attempt-fence-final --tb=line

.venv312/Scripts/python.exe -m ruff check quantgpt/task_store.py quantgpt/task_executor.py quantgpt/research/jobs.py quantgpt/mcp_task_helper.py quantgpt/routes/backtest_tasks.py tests/test_durable_tasks.py
.venv312/Scripts/python.exe -m pyright quantgpt/task_store.py quantgpt/task_executor.py quantgpt/research/jobs.py quantgpt/mcp_task_helper.py quantgpt/routes/backtest_tasks.py
```

精确最终计数及性能基准以本轮 [执行状态](US_RESEARCH_EXECUTION_STATUS.md) 收口记录为准。源码变更会改变本地引擎身份；缓存竞态修复后须重新核对性能基准的代码身份。SQLite 故障注入成功不能替代 PostgreSQL、多进程部署、真实平台或真实全量行情验收。
