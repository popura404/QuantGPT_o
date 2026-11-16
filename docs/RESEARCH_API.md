# 共享研究 API 与 MCP

所有新 HTTP 路径以 `/api/v1/research` 开头，需要现有登录令牌。项目 ID 选择上下文，不能替代认证。成员撤销、viewer 写入、跨项目产物和未注册快照均由服务端检查。MCP HTTP 支持现有部署 token 的服务端身份映射，以及 JWT/API key 解析 principal；stdio 使用服务端 `QUANTGPT_MCP_STDIO_USER_ID`，未配置时为专用系统用户。须由项目管理员显式邀请系统身份，不能共享所有用户的私人收藏。

| 方法/路径 | 输入与结果 |
|---|---|
| GET/POST `/projects` | 可访问项目列表/创建项目 |
| PUT `/projects/{id}/members` | 管理员设置成员角色或移除成员 |
| POST `/projects/{id}/offline-demo` | 固定种子合成行情、实际冻结 manifest、definition/config/template；明确 synthetic |
| POST `/projects/{id}/hypotheses` | definition、economic_hypothesis、可选 frozen_baseline；在该定义的任何评价登记前冻结 |
| POST `/projects/{id}/holdout` | start/end；在项目研究开始前登记不可变最终窗口 |
| POST `/projects/{id}/evaluations` | definitions/config；同批一次面板构建，摘要+artifact refs，相同哈希复用 |
| GET `/projects/{id}/evaluations/{evaluation_id}` | 紧凑结果与冻结配置 |
| GET `.../evaluations/{evaluation_id}/validation?profile=local_factor` | local_factor/local_strategy/wq_remote 的必需、可选、不适用证据及阻断 |
| POST `.../evaluations/{evaluation_id}/validation-runs` | 可选 selection_evaluation_id/idempotency_key；202 持久离线验证任务；不接受调用者提供 checks |
| GET `/projects/{id}/artifacts/{artifact_id}` | 授权后核验内容哈希，按需读取大数组或研究卡 |
| POST `/projects/{id}/strategy-runs` | strategy_spec/v2 + config；服务器核验组件引用、版本、数据、方向和窗口 |
| GET `/projects/{id}/strategy-runs/{run_id}` | 不可变策略记录 |
| POST `.../strategy-runs/{run_id}/export` | 无调用者证明；服务器验证后返回导出或具体阻断 |
| POST `/projects/{id}/portfolio-optimizations` | 保存的 signal_ref + 优化配置；约束残差、基线/成本诊断、产物引用 |
| POST `/projects/{id}/tasks` | request.kind=factor/strategy/validation + 对应请求 + 可选 idempotency_key；202 持久接受 |
| POST `.../evaluations/{evaluation_id}/reconcile` | WQ 已知远端引用仅 GET 对账，未知结果不盲重 POST |
| POST `.../evaluations/{evaluation_id}/cancel` | 停止 WQ 本地等待；不冒充平台已取消 |
| GET `/capabilities` | 实际注册市场、WQ 能力及未验证范围 |

任务继续使用 `/api/v1/tasks/{task_id}` 查询、取消与恢复入口。持久 outbox 在接受前提交，worker claim/lease/attempt/revision 保护结果发布，失去权限或取消后不能用旧 worker 覆盖终态。WQ 远端未知、对账需要和本地停止等待单独显示。原始日志、行情、SQLite、快照、前端产物保留本地且由 `.gitignore` 排除。

MCP 新工具：`list_research_projects`、`evaluate_factors`、`run_research_strategy`、`get_research_artifact`、`optimize_research_portfolio`、`export_research_strategy`。评价/策略工具默认 `submit_only=true`，返回 task_id；读取评分与产物不会重跑回测。旧入口保留读取和研究行为，但旧 payload 不能靠填写 `promotion_ready` 或 validation booleans 获得合法导出。

本地引擎身份由实际 Python 版本、全部 `quantgpt/**/*.py` 内容、已安装数值依赖版本、操作系统/架构与当前平台依赖锁摘要计算，替换调用者的引擎声明。更换 NumPy/pandas 等依赖会改变结果身份；已接受的持久任务遇到计算身份变化会要求重新提交。表达式必须声明所有使用的字段契约。输入快照按项目登记并核验实际文件和逻辑内容；仅知道 manifest hash 不构成读取授权。合成数据和免费演示数据的 `research_only` 不会因设置布尔值而升级。

本轮本地完整验证 profile 仍有未实现/未获得的独立证据，合法导出默认受阻。PIT 风险暴露、完整真实公司行动/退市终值、正式历史证券库和充分独立验证没有被合成测试代替。

## 离线验证闭环（2026-10-18）

网页、HTTP 和 MCP `validate_research_evaluation` 共用持久任务服务。输入只引用已有成功本地 evaluation，可选引用在 final 之前完成的同一冻结候选 selection。服务端核验当前权限、真实引擎身份、数据快照、收益 artifact 哈希、final 预留记录后，用冻结方向、费用、窗口和实际回测入口重放。部署代码变化必须先重算评价。

结果独立保存为 `offline_validation` artifact。当前报告策略为 `offline_validation/v3`，报告身份包含来源哈希、selection 引用、验证政策和试验清单。已冻结轮次始终使用其封存清单。普通项目增加已观察试验后，旧报告可能返回 `OFFLINE_VALIDATION_STALE_TRIAL_COUNT`。旧版本报告保留原始内容及哈希，读取时附带历史状态。GET validation 返回当前适用的检查、阻断及报告引用。

检查包括收益重放、OHLCV、标签边界、成本配置、策略目标权重约束与组件血缘。至少60会话的已预留 final、fresh_cash 情形会分三段重新运行固定候选，报告明确不是滚动重新训练。净收益块符号随机化只作零假设诊断，Bonferroni 使用服务端完整观察试验数；它不代替截面因子 placebo 或基准超额收益验证。没有预登记比较库的 similarity、短样本 walk-forward 等保留 `insufficient_data`，不补造 passed。

### 外部行情证据

先用 `register_research_snapshot` 授权项目使用行情快照。部署方将核过来源的 `external_market_evidence/v1` JSON 发布至 `QUANTGPT_RESEARCH_EVIDENCE_ROOT/<manifest_id>/evidence.json`。此目录由部署方管理并向服务提供只读访问。MCP 和 HTTP 调用只需评价 ID。侧车中的 `manifest_id`、`content_sha256`、完整 `scope`、`coverage_start` 与 `coverage_end` 绑定原快照及被评价的输入历史。每项证据的 `provenance` 保存 `provider`、`dataset`、`release`、`source_ref` 和部署方审阅过的 `license_ref`。实际行情样本的生成器示范了缺证时如何保留可核对的部分。

| 节点 | 终验核对内容 |
|---|---|
| `prices` | 对照原始 OHLCV、币种和复权口径，逐证券逐日比对交易所日历、记录和可见时间 |
| `historical_universe` | 对照稳定证券 ID、ticker、成员生效区间及当时可知的时间。固定池还需有首次使用前登记的选择时间 |
| `corporate_actions` | 逐证券核对分拆、现金分红、支付日、退市终值及整个窗口的事件覆盖 |
| `benchmark` | 核对预登记总回报基准、币种和股息再投资口径，逐日重算收益并核对连续指数点位 |

对已登记的评价运行 `validate_research_evaluation`，或完成 `finalize_research_round`。查看 `validation.external_acceptance.checks` 中的分项状态、原因、记录数和来源。`not_run` 表示缺少该项证据，`insufficient_data` 表示提供了证据但覆盖或授权不足，`failed` 标识矛盾或无效输入。全部核对通过及来源可用时，外部状态才为 `passed`。策略导出还会核对独立策略验证与引擎状态。

部署方原子替换证据文件后可对原评价重新运行验证。报告、验证任务及导出判断均绑定文件字节的 SHA-256。过期报告保留原内容与摘要，新证据可以复用原始收益和冻结试验分母。研究期与留出期的原始收益和行情不会写入外部证据摘要。

## 项目准备与组合基线

| MCP 工具 | HTTP 路径（相对 `/api/v1/research`） | 用途 |
|---|---|---|
| `create_research_project` | POST `/projects` | 创建当前身份拥有的研究项目 |
| `get_research_preparation` | GET `/projects/{id}/preparation` | 查看已登记假设、最终窗口和本项目快照的实际字段、日期及覆盖 |
| `list_research_snapshot_imports` | GET `/projects/{id}/snapshot-imports` | 分页发现管理员共享的面板清单与字段，`next_cursor` 作为下一页的 `after` |
| `register_research_snapshot` | POST `/projects/{id}/snapshots/{manifest_id}` | 导入服务器共享目录内的冻结面板，返回可用于 evaluation 配置的 `data_input` |
| `prepare_research_demo` | POST `/projects/{id}/offline-demo` | 生成合成面板与示例配置以验证工程流程 |
| `register_research_holdout` | POST `/projects/{id}/holdout` | 在项目第一次评价前登记终验区间 |
| `register_research_hypothesis` | POST `/projects/{id}/hypotheses` | 在该因子第一次评价前冻结经济假设 |
| `compare_research_strategies_to_baseline` | POST `/projects/{id}/rounds/{round_id}/comparisons/{augmented_run_id}` | 比较已保存的增强策略和该轮预登记基线，返回证据与 artifact 引用 |

管理员用 `QUANTGPT_RESEARCH_IMPORT_ROOT` 指定可向项目公开的冻结面板目录。该目录与 `QUANTGPT_RESEARCH_SNAPSHOT_ROOT` 各自独立。共享目录包含 `freeze_market_frame` 生成的 `ds_…/manifest.json` 与 `market.parquet`。项目写成员可以按清单 ID 导入，共用目录的内容应已获准向所有项目研究成员共享。服务端校验文件字节和逻辑内容哈希后，保存一份可重放的私有副本。已有授权可随时重新核验。返回的 `asof_basis=server_snapshot_registration` 指服务器登记版本的时刻。价格基准、历史股票池、公司行动和总回报基准的外部证据仍需单独验收。

准备基线时，先登记项目最终窗口，在 selection 窗口运行基线策略，然后在创建研究轮次时绑定该 `baseline_run_id`。由研究方向提出新增组件，在相同冻结快照、selection 窗口、费用和调仓规则上运行增强组合。比较要求保留基线全部组件及它们之间的相对权重。结果包含两组复合净收益、配对日收益差、移动块重抽样区间、按证券交集计算的日截面秩相关及覆盖量。区间需要足够的交易日。每日换手取买卖成交额之和的一半，再除以该日的期初净值，最后按交易日取平均。这些度量描述选择期的增量表现，独立终验在轮次冻结后单独运行。

## 冻结行情、信号时点与成交口径

本地因子、组合和验证重放使用项目登记的面板。固定池使用面板内的证券，支持自建 `universe_id`。美股市场支持 `us_equity` 和 `us`。动态池须提供稳定的 `security_id` 和冻结的 `universe_membership` 记录，每条包含 `security_id`、`valid_from`、`valid_to`、带时区的 `available_at`。成员区间左闭右开。本地执行器校验每个决策日面板内的证券与当时已知的成员集合完全一致。只有固定证券样本时，填写 `universe_mode=fixed_cohort`。

日频信号默认在登记交易所当日实际收盘时刻计算，包括提前收盘日。自定义时点可在面板中提供带时区、同一会话收盘后或收盘时的 `decision_at`。面板的 `available_at` 和所用字段各自的 `<field>_available_at` 必须不晚于决策时刻。使用财报或其他声明 `requires_vintage` 的字段时，每个非空观测还需提供 `<field>_revision_id` 和字段级可用时点。明确迟发的输入会使该次计算失败。无法核验一般字段发布时间时，报告通过 `signal_availability.unverified_fields` 列出字段，发布证据中的可见时间和授权会在 `external_acceptance.checks.prices` 中独立报告。

快照准备从供应商声明、冻结元数据及行内 `adjustment` 核对价格口径。确认未复权时，优化器可以用普通 `open`、`close` 读取原始价格，并结合 `volume` 估计流动性。分红在除息日进入应收及净值，在支付日进入可用现金；持仓按已结算资金和可售资产执行。

## 多方向协作轮次

使用 Alembic 管理的数据库先执行 `alembic -c alembic.deploy.ini upgrade head` 升级到 018，再启动 MCP 服务。

一轮研究共享项目、选择期、快照、模拟配置和一次最终留出窗口。项目所有者或管理员创建轮次，保存 `coordinator_key`，将每个方向的 `direction_id` 和对应的 `direction_key` 交给负责该方向的子代理。共用一个 MCP 身份时可省略方向的 `actor_id`。使用不同登录身份时，创建轮次时指定相应项目成员的 `actor_id`。同一项目有一个受管轮次，项目成员可以查询进度。若需要恢复创建请求，首次创建时传入自行生成并保存的随机 `coordinator_key`，长度至少 32 个字符。

| MCP 工具 | 调用者与输入 | 结果 |
|---|---|---|
| `create_research_round` | 协调者传入项目、`config`、`holdout_start`、`holdout_end`、各方向的 `name` 和 `hypothesis`，可指定已回测的 `baseline_run_id` | `round_id`、协调者凭据和各方向凭据 |
| `check_research_factor_novelty` | 传入 `project_id` 和 1–100 个候选 `expressions` | 每项返回完全匹配及同族参数变体的数量、来源示例和 ID，以及可开发的 `new_indices` |
| `submit_research_direction` | 各子代理传入轮次、方向凭据和 `definitions`，可指定 `idempotency_key`、`direction`、上游 `parent_evaluation_id` 和 `only_new` | 新意筛选模式 `only_new=true` 在同项目的提交事务内查重；默认模式对相同定义及配置复用计算、各方向分别署名 |
| `import_research_iteration` | 子代理传入方向凭据和已有迭代任务的 `task_id` | 将整批已观察候选登记为该方向的研究历史 |
| `cancel_research_round_task` | 子代理用方向凭据取消自己的任务，协调者用轮次凭据处理指定任务 | 取消结果或当前终态；协调者可用原 `selection_run_id` 再次调用终验，恢复同一冻结任务 |
| `finish_research_direction` | 子代理使用自己的凭据结束提交，协调者也可接手关闭离线成员的方向 | 关闭该方向的新提案，记录关闭人；终验仍需等待在途任务结束 |
| `get_research_round` | 项目成员传入 `project_id` 和 `round_id` | 方向假设、各提案表达式与结果引用、指标、失败原因和当前试验数；冻结后返回固定清单和报告引用 |
| `submit_round_strategy` | 协调者提交组合的 v2 `spec`、组件引用和协调者凭据 | 选择期策略回测任务，完成后取得 `strategy_run_id` |
| `finalize_research_round` | 协调者传入 `selection_run_id` 和协调者凭据 | 冻结入选策略及试验集合，提交单次 final 任务 |

对每个后台任务使用 `get_mcp_task_status(task_id, include_result=true)` 取回结果。选择阶段可以运行多个候选组合。存在预登记基线时，在终验前用 `compare_research_strategies_to_baseline` 查看新增因子的配对净收益差、区间与换手，再决定冻结哪一份选择期结果。最终任务的 `validation` 包含 OOS、滚动窗口、试验校正和数据验收状态。用 `get_research_artifact` 及 `validation_artifact.artifact_id` 可再次读取同一份报告。

查重涵盖当前项目待运行与执行中的本地因子任务、已导入的迭代记录，以及当前身份可访问的因子池、本地选择期评价、已完成的平台评价、成功的历史尝试、个人收藏和本人已提交的平台因子。空格、已支持的算子别名归一；改窗口参数会提示同族，仍可作为新变体提交。它衡量公式重复程度，经济暴露相关性需另行比较。只读查询不登记试验。若一批中已有公式或批内重复，`only_new=true` 返回 `known_candidates`，整批不建任务，按结果中的下标重提新表达式即可。两个方向同时提交时重新检查，同一请求重试返回原任务。

创建轮次会检查标签周期与持有周期、初始资金模式，以及两个窗口需要的交易日。已在项目运行的选择任务需先结清。导入旧迭代时保存有成绩的父回测和候选（含取消前的成功观察），并按身份去重。如果旧研究已看过留出期，或缺少判断独立性所需的历史范围，终验在使用留出窗口前返回原因；已有来源补齐后可重复导入。

带可用时间的行情按信号时刻检查，使用需历史版本的字段时也要提供字段级可用时间与版本。组合按登记的交易日历对齐基线和增强策略，预热不足时保留现金。最终报告在留出期内核对仓位，区间内无可验证的投资仓位会返回证据不足。原始价格、历史成员、公司行动及总回报基准的外部验收状态仍由报告单列。
