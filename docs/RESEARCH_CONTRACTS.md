# 研究契约 v1

日期：2026-10-05。对应执行计划 P01。实现：`quantgpt/research/contracts.py`。
本文件冻结新研究请求及后续实现的语义；它本身不证明既有回测、数据库、远程平台或真实美股数据已经遵守这些契约。
测试使用手算合成输入，`external_integration=not_run`。旧 `factor_hash`、v0/v1 策略与报告含义不改变，修复后的计算结果需要新 evaluation；旧净值不可升级成新证据。

## 版本与身份

| 对象 | 版本/规则 |
|---|---|
| 字段契约 | `research_fields/v1` |
| 本地算子 | `factor_semantics/v2`，导出常量 `SEMANTICS_VERSION` / `OPERATOR_SEMANTICS_VERSION` |
| 定义 | `factor_definition/v1` / `FactorDefinitionV1` |
| 评价配置 | `evaluation_config/v1` / `EvaluationConfigV1` |
| 本地模拟配置 | `simulation_config/v1` / `SimulationConfigV1` |
| 引用 | `artifact_ref/v1`、`evaluation_ref/v1`、`signal_ref/v1`、`strategy_run_ref/v1` |
| WQ | `backend=wq`、`scope=platform`，使用实际平台语义版本；未知时明确 `wq_platform/unknown` |

`definition_hash(definition)` 包含规范化表达式、语言、字段经济含义/单位/可用时点和算子版本。
规范化只消除 token 间空白、按字段名排列字段；不重写代数、不猜测别名等价，也不代替表达式安全校验。
`evaluation_hash(definition, config)` 包含 definition hash 和完整配置：scope、股票池/日历版本、窗口/split、方向、中性化、warmup、label horizon/purge、simulation_config、全部数据 manifest/asof、引擎/代码/语义版本、远端 settings。
`strategy_hash(spec, config, evaluations)` 包含完整策略 spec、配置和有序组件证据。字段/成本/方向/源状态变化都创建新身份。

哈希使用带域前缀的 SHA-256，JSON key 排序、紧凑 UTF-8 编码，拒绝 NaN/Infinity；不声称字符串日期/数字类型的任意表达形式等价。
DTO 的 frozen 限制不代替数据库不可变存储；服务端必须保存完整规范化配置和 manifest，不能原地修改已完成 evaluation。
project 权限不是 hash 的替代品，跨项目同内容缓存仍需当前成员授权。evaluation/strategy 的最终可信状态只由服务端登记产生。

## 字段单位、行身份和可用性

内部行键为 `(security_id, session)`，同一键重复必须拒绝，ticker 只是有效期属性。兼容入口可显式把 stock_code/trade_date 映射为这两个键。
每个值需区分 economic period、effective interval、`public_at`、`available_at`、`vintage_id` 与 `retrieved_at`；抓取时间不等于公众首次可用时间。
`available_at <= signal_time` 才允许进入该次特征。收盘后公告不得用于当日已发生的交易。
只有公开日期而无时刻时，最早可用设为公告日之后的下一交易会话开盘，再按实际 provider latency 延后；本地 after_close 信号因此最早在该会话收盘生成。

`US_RESEARCH_FIELDS` 是目标字段目录，并非某 provider 的供给声明：

| 字段 | 单位 / 经济含义 | 时间与复权 |
|---|---|---|
| open / close | USD/share，原始开/收盘价 | 当次会话成交公布后；raw |
| volume | shares，该会话成交股数 | 不是金额或流通股数 |
| shares_outstanding | shares，已发行普通股数 | PIT vintage，和 raw price 使用同一拆股口径 |
| market_cap | USD，raw price × 同口径 PIT shares | 两个输入都可用之后；禁止 close 或 close×volume 代理 |
| industry | 版本化行业分类代码 | effective interval 与 known-at 双重约束 |
| revenue | USD，离散季度营业收入 | PIT 披露，不等同净利润 |
| net_income | USD，离散季度净利润 | 不等同经营利润或现金流 |
| equity | USD，时点权益 | 不等同资产 |
| suspended | boolean，已知不可交易状态 | 按事件生效/公布时间 |

季度、年报、TTM 必须另列 period；不能把累积年内数直接累计为四季 TTM。财务比率规定 ratio 或 percentage 后才能映射。
行业/市值中性化调用 `CapabilityReport.require_neutralization()`；缺字段抛 `CapabilityBlockerError`，携带 `error_code/retryable/next_action/affected_fields`。
未知不是可用；字段名字匹配不证明经济含义匹配，provider 需验证单位/期间/复权/vintage 后才可声明 available。

## 算子和预热

共享样例：`tests/fixtures/research/operator_golden.json`。NaN 用 JSON null，正负无穷用字符串编码。

| 算子 | 分组 / ties / 缺失 / 输出 |
|---|---|
| rank(x) | 每 session 截面；average ties，rank/n_valid；NaN/inf 不参与分母且原位置缺失；单值为1 |
| scale(x) | 每 session 的 `(x-min)/(max-min)`；常数或全零截面有限项为0；缺失保持缺失；全缺失输出全缺失 |
| ts_rank(x,n) | 同一 security 按 session 排序的最近 n 行，最后值的 average percentile；不可跨股 |
| ts_mean/std 等有限窗口 | 同一 security 的有序历史；保留 legacy evaluator 的 partial-window 特征输出，但研究评分窗必须满足完整嵌套 lookback |
| BOLL | 同一 security 的均值与标准差，标准差样本 ddof=1；上下轨为 mean ±2std，不跨股 |
| 中性化 | 同 session，按主键回填残差；不使用 `.values` 覆盖不同顺序行；行业/市值缺失明确阻断 |

scale 保持本项目的 min-max 定义，不冒充 WQ 同名算子，也不具有时序窗口含义。
滚动缩放如后续新增必须另起名字和明示窗口。
非有限值统一视为缺失，不通过清零产生有效信号。该政策也适用于算术后产生的 inf。

`required_observations` 包括当前观测，`prior_sessions = required_observations-1`：字段=1、截面算子不增加历史、有限窗口=子需求+n−1、lag=n+子需求、双输入取最大子需求。
`ts_mean(ts_mean(close,20),60)` 要79个观测，不是60个；horizon purge 至少覆盖真实 label horizon。
partial-window 输出可用于兼容展示，不得因其非 NaN 就进入新研究评分；不足历史的资产需 mask。
warmup 只提供过去特征/估计状态，不计入评分窗收益；增加未来不能改变过去输出，行顺序变化不得改变按主键对齐的结果。
EMA/MACD 等递归算子没有有限精确截断，必须另记初始状态、历史起点与容差；有限 lookback 不是其完整重放证明。

## 模拟时序和资金核算

新 `SimulationConfigV1` 放在研究运行请求的 `simulation_config` 中。旧 `strategy_spec/v0` 和 v1 继续拒绝这个新增字段；新 StrategySpec v2 由集成层显式接入。
禁止塞 `execution` 或 broker/account/order/api_key/python_code/script/callback_url；递归拒绝这些键，包括远端 settings 和额外 strategy 字段。

唯一事件顺序 `EVENT_ORDER`：

1. 读取上一收盘数量、现金与应收，作为今日起始权益。
2. 公司行动和权益确认：按原持仓处理拆股、除息应收；支付日将应收转现金。
3. 当日开盘按上一收盘后的可用信号成交，按实际买卖金额分别扣费。
4. 用独立完整估值表的 raw close 估值数量+现金+应收。
5. 收盘后按 available_at 生成供下一会话开盘使用的信号。

当天开盘新仓只获得开盘后的价格变化，不获得此前隔夜收益或除息权益。
`raw_prices_cash_and_receivables` 不能再叠加 total-return 调整价格，防止分红双计。
无调仓数量保持不变，不能把旧等权目标每天当成实际等权持仓。
`fees_bps` 与 `slippage_bps` 都按实际绝对买/卖金额计；首笔建仓收费，cash rate 默认为0，long-only 且不允许透支/杠杆。
调仓锚点在完整 calendar session 序列上确定；对训练/OOS 截窗仅切同一序列，不能重新从窗口第一天计数。

缺成交价默认 block，可显式 skip_and_hold；缺估值价必须 block，不能记零收益或删掉已有持仓。
拆股/现金分红按上述账本处理；现金并购、换股、分拆及终值未知必须由执行引擎显式报告支持，不支持时 block 受影响研究。
协议不因存在公司行动字段就自动宣称执行引擎支持该事件。

`fresh_cash` 开始时仅有正现金、无仓位/应收/源引用；首笔费用属于本评分窗。
`carry_forward` 必须包含起始数量/现金/应收、前一状态 session 和 content-addressed `starting_state_ref`，服务端核对实际状态内容与引用一致。
源状态必须早于评分窗；不因切窗平仓重建，也不重复收取已有仓位建仓费。
起始状态及来源一同进入 evaluation hash。两种状态经济路径不同，不强求相同收益。
初始 NAV 是回撤高水位的一部分，因此首日费用/亏损计入最大回撤。

基准单独记录 total-return 口径、provider 的分红再投资时点和基准成本（默认0）；不能把原始价格指数称为含分红收益。
基准与策略使用相同会话/币种，基准再投资规则可能不同，报告必须说明。

手算事件样例：`tests/fixtures/research/simulation_golden.json`。其中从1000现金以110买5股、10bps费用、收盘121，现金449.45、净值1054.45；不享有买入前每股2美元分红。
carry_forward 的5股在100→98除息时确认10应收，现金500+股票490+应收10保持NAV1000。
这些是后续账本的同一组 oracle，不是已连接的回测证据。

## 日历、数据与后端能力

`TradingCalendar.sessions(start,end)` 返回唯一有序 `Session(session, open_at, close_at)`；open/close 必须是含时区时间戳，`next_session()` 按交易所节假日/半日市定义，禁止简单 weekday/date+1 替代。
calendar_id、version、timezone 进入研究身份。协议接受夏令时/半日市实例，真实日历正确性仍需 provider fixtures 与实测。
warmup、horizon、purge 和调仓均按这个共同 session 序列，不按某只股票缺行后的序号。

数据 manifest 覆盖价格、字段值、vintage、股数、行业、成员资格、可交易性与公司行动及内容哈希；raw/signal/valuation 调整口径分开。
`DataInputRef.version_status=verified` 要求 manifest_id 和 content_sha256，仍由快照服务核对文件内容。
本地 data version 或引擎 conformance 未验证时 `evidence_eligibility=research_only`；齐全时仅为 eligible_for_validation，不代表通过。
WQ 只返回 platform_only，底层版本未知仍可保存真实 remote_run_ref 和平台结果，但不得当作本地 final 证据。
`ResearchStatus` 分别保存 contract_verified、external_integration 与 research_decision，任一通过不自动改变另外两项。

## DTO、授权和状态转换

`SignalRef` 由信号 artifact、evaluation 引用、session、available_at 组成；两个引用的 project 必须一致，artifact kind 必须 signal。
`EvaluationRef` 明确 evaluation_id/definition_hash/evaluation_hash/backend/scope；`StrategyRunRef` 明确 strategy_run_id/strategy_hash/evaluation_hash/scope。
客户端提交这些引用只是查找请求；服务端重新加载对象，核对 hash、版本、scope、状态与引用，拒绝伪造 passed 布尔值作为晋级/导出证据。

HTTP principal 从认证凭据解析，stdio 从服务器受控连接配置解析，worker 从任务冻结的 actor/project 解析；project_id 只是上下文选择。
`PrincipalContext` 校验结构与 transport 对应，但不能代替认证，禁止从 HTTP 请求正文实例化后直接信任。
`authorize_reference()` 先校验项目一致，再通过 `ProjectAuthorizer.require_current_membership()` 每次重查当前权限；成员被移除后不能继续通过老 token、任务快照或缓存读取。
创建任务冻结 actor/project；读取任务/产物/缓存和最终写入仍重查权限。P07/P08 将此协议接入数据库；当前授权测试使用最小内存 authorizer，只证明调用契约。

`TASK_TRANSITIONS` / `validate_task_transition()` 冻结后续持久任务的状态规则：

| 当前状态 | 允许下一步 |
|---|---|
| queued | running / cancel_requested / cancelled |
| running | completed / failed / cancel_requested / interrupted / remote_outcome_unknown |
| cancel_requested | cancelled / local_wait_cancelled / remote_cancel_confirmed / interrupted；禁止 completed |
| remote_outcome_unknown | reconciliation_required / cancel_requested；禁止自动重新 queued |
| reconciliation_required | 由查询结果恢复 running/completed/failed 或 remote_cancel_confirmed |
| local_wait_cancelled | 可继续对账或确认远端取消；不声称远端计算停止 |
| completed / failed / cancelled / remote_cancel_confirmed | 不可复活 |

基础设施重试创建 attempt 或经受控 interrupted 恢复；新的研究输入创建新评价。平台无幂等/对账能力时结果未知需人工处理，不盲重发 POST。
关键结构化错误：`CAPABILITY_FIELD_UNAVAILABLE`、`CAPABILITY_FIELD_UNKNOWN`、`PROJECT_SCOPE_MISMATCH`、`INVALID_TASK_TRANSITION`；执行服务另补数据缺价/缺来源/远端限流等 error_code，并统一返回 retryable/next_action。

## 集成和验证边界

契约测试：`python -m pytest tests/test_research_contracts.py -q`。覆盖定义/evaluation/strategy identity、fresh/carry 拒绝规则、禁止实盘字段、v0/v1 兼容读取、unknown 数据、WQ scope、purge、缺真实中性化字段、引用/权限重查、状态终态。
operator golden 由实际解析器测试消费；simulation golden 必须由实际账本测试消费，不能用读取 JSON 本身冒充执行验收。
`models.py`/迁移、adapter DataField 增量字段、StrategySpec v2/运行请求和 API/MCP 接入由集成负责人统一修改。
后续若修改冻结语义或缺失政策，需要升级 semantics/config version 并保留旧报告及重算关联。
