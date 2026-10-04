**QuantGPT 美股双路线研究平台执行计划**

执行记录见 [US_RESEARCH_EXECUTION_STATUS.md](US_RESEARCH_EXECUTION_STATUS.md)。用户已将本轮优先范围明确为离线契约测试、免费数据简单验证和付费数据接入口；下述完整发布门禁仍保留，不能把离线通过等同于真实美股/WQ生产验收。

版本：v1.1，已完成写后审核及修订；日期：2026-10-05（Asia/Shanghai）；代码基线：`25bda5e`。

依据：[项目审阅报告](E:/fpga/量化gpt/docs/reviews/2026-10-05-us-factor-strategy-audit.md)。目标是同时支持本地美股因子/策略研究和 WQ BRAIN 因子研究，优先打通共同的因子池、实验记录、验证证据与工作台。本文件安排后续实施；所有工作包初始状态为 `planned`，已有审阅及复现不代表修复完成。

**1. 交付目标与范围**

完成后的两条主流程：

```text
本地美股：研究项目 → 冻结数据/研究配置 → 因子分析 → 候选因子池
         → StrategySpec → 成本与风险回测 → 独立验证 → 候选信号/报告

WQ BRAIN：同一研究项目/因子定义 → 远程能力检查 → 远程模拟
         → 平台结果归档/比较 → WQ 专属检查及既有提交门禁
```

共用研究身份、血缘、状态和工具生命周期；本地与 WQ 各自保留数据范围、参数含义与验证依据。WQ 成绩不代替本地策略验证。本轮首个美股发布范围为日频普通股、USD、long-only/long-cash 研究组合；单独标识 ETF、ADR、OTC 等未纳入类别。保留现有 A 股研究入口并做回归验证。

输出为研究策略、目标权重、候选信号和报告，沿用仓库现有边界；不在这份计划中新增券商连接、真实订单或实盘自动执行。WQ 现有正式提交能力保留其独立门禁，开发/CI 使用 mock 或获准保存的响应，不触发真实提交。

发布成功的判断是计算可信、证据完整、流程能复现；真实策略即使最终被拒绝，也应正常产出拒绝原因。不能把“必须找到高收益因子”设成工程验收条件。

**2. 先固定的设计决定**

| 决定 | 执行约定 |
|---|---|
| 环境基线 | 首先以现有 CI 的 Python 3.12 建立 Windows/Linux 可重复环境，锁定经过测试的依赖；短期保持 MCP 1.x API 兼容。其他 Python 版本在通过矩阵后再声明支持 |
| 算子语义 | 每个算子记录截面/时序分组、ties、NaN、窗口和输出范围；逐日 rank/scale 与滚动缩放分开；修复后增加 semantics_version，不静默覆盖历史实验 |
| 模拟契约 | 新研究配置明确 signal_time、available_at、fill 时点/价格、现金、费用、调仓会话、缺价和公司行动政策；第一版可执行模拟用收盘后信号、下一交易日开盘撮合。evaluation_start_state 为 fresh_cash 或 carry_forward，连同起始状态来源进入 evaluation_hash |
| 数据身份 | 稳定 security_id 为主键；ticker 为有生效期的属性。固定 cohort 和动态 universe 是两个明确模式；财报保存 vintage 和可用时刻 |
| 因子与实验身份 | 新 definition_hash 只标识规范化定义/字段契约/语义版本；evaluation_hash 标识定义+完整配置+数据输入+引擎版本；strategy_hash 标识完整策略。不重新解释旧 factor_hash |
| 公共身份 | 复用现有用户与实验/策略表，增加 project 归属和成员关系。HTTP MCP 从认证凭据解析 principal，本地 stdio 从服务端受控连接配置解析；project_id 只选择上下文，不代表身份。不能通过取消用户隔离来“打通因子池” |
| 证据来源 | 晋级、策略导出以服务端 evaluation_id/strategy_run_id 查证据，不接受调用者提交的通过布尔值作为证明。scope、hash、版本必须一致 |
| 兼容策略 | 保留 v0/v1 读取、旧报告和原始 payload；新模拟契约、策略血缘和验证阶段用明确版本承载，拟新增 strategy_spec/v2 与 strategy_signal.v2，并同步校验器/前端/文档 |
| 可重现性 | 数据 manifest 覆盖行情、财报 vintage、股数、行业、成员资格、可交易状态和公司行动；代码/引擎/算子版本另外进入评价身份；缓存据此分层 |
| Rust | 未通过差分一致性测试的算子默认不用于可信计算；允许 Python 先发布，Rust 能力必须显示实际验证状态 |

注意：当前 schema 递归禁止 `execution` 等字段。模拟假设使用研究运行请求中版本化的 `simulation_config`，同步更新允许字段，保留 broker/account/order 等禁用项；不能直接向 v0/v1 塞一个会被拒绝的 execution 对象。

后端能力在执行前分别声明，未核实的能力填 `unavailable/unknown`，不得按名称推断：

| 能力 | 本地美股后端的目标 | WQ 后端的处理 |
|---|---|---|
| 研究日期/独立 holdout 控制 | 使用本系统冻结的 split 与最终测试锁 | 按平台实际能力；不能把平台 IS 检查称为本系统独立 final |
| 原始收益/因子序列 | 可追溯 artifact，用于统计与再验证 | 只对实际取得的序列计算检验，不从汇总 Sharpe 臆造序列/统计量 |
| 底层数据版本 | 完整本地 manifest | 无法取得时记 unknown，保存平台请求/结果/remote_run_ref |
| 平台检查 | 不适用，使用本地研究验证 | 保存平台原始结果和 scope，与本地 promotion policy 分开 |
| 远程幂等/取消/对账 | 本地持久任务负责 | 仅平台明确支持时启用；结果未知时先对账，不能盲重发 |

完成状态分开记录 `contract_verified`、`external_integration=not_run/verified/blocked` 和 `research_decision`。代码契约通过、真实外部接入通过、研究候选通过是三个不同结论。

**3. 工作包、依赖与责任边界**

角色：集成负责人负责 schema、迁移和跨模块收口；计算负责人负责因子/账本；数据负责人负责数据契约和美股；服务负责人负责实验/任务；前端负责人负责研究工作台。可由主代理和子代理承担；同一文件在同一批次只交给一个写入者，其他人提供只读评审。

下表依赖是合入完成版的前提，接口草案、fixtures、UI 原型可提前并行。门禁 G0–G4 定义见第 5 节。

| ID | 工作包 | 依赖 | 主责 | 目标门禁 |
|---|---|---|---|---|
| P00 | 环境、测试和兼容基线 | — | 集成 | G0 |
| P01 | 研究/模拟/字段/算子契约 | P00 | 集成+计算+数据 | G0 |
| P02 | 因子算子与中性化正确性 | P01 | 计算 | G1 |
| P03 | 数据资格、字段语义与完整快照 | P01 | 数据 | G1 |
| P04 | 持仓账本、时序和成本 | P01 | 计算 | G1 |
| P05 | 信号、指标、warmup 与切窗一致性 | P02、P03、P04 | 计算 | G1 |
| P06 | Python/Rust 差分与安全选择 | P02、P05 | 计算 | G1 |
| P07 | 共享项目身份、因子池迁移及最小网页入口 | P01 | 服务+前端 | G2 |
| P08 | EvaluationRun 与证据/产物存储 | P03、P07 | 服务 | G2 |
| P09 | 统一验证、搜索计数与最终测试锁 | P05、P08 | 计算+服务 | G2 |
| P10 | 策略血缘、版本与合法导出 | P04、P05、P08、P09 | 服务+计算 | G2 |
| P11 | 持久任务、取消、恢复与幂等 | P01、P07 | 服务 | G2 |
| P12 | WQ 研究记录接入公共流程 | P08、P09、P11 | 服务 | G3 |
| P13 | 美股证券/行情/公司行动适配器 | P01、P03、P11 | 数据 | G3 |
| P14 | 美股 PIT 财务、股数与行业 | P03、P13 | 数据 | G3 |
| P15 | 因子研究卡与增量贡献分析 | P05、P08、P09、P13、P14 | 计算 | G3 |
| P16 | 组合约束、风险与成本优化 | P10、P13、P14、P15 | 计算 | G4 |
| P17 | 研究工作台与端到端体验 | P07、P10、P11、P12、P13、P15、P16、P18 | 前端 | G4 |
| P18 | 统一评价工具、结果引用和批量缓存 | P08、P09、P10、P11、P12 | 服务+计算 | G4 |
| P19 | 性能基准和有证据的加速 | P06、P13、P14、P18 | 计算+服务 | G4 |
| P20 | 发布回归、迁移演练与文档 | P12、P13、P14、P15、P16、P17、P18、P19 | 集成 | G4 |

每个包拆成独立可审阅变更，记录 `planned → in_progress → in_review → verified`；运行失败或外部数据缺失时记录 blocker 和受影响范围。`verified` 必须附提交号、测试命令、数据/配置版本和产物，不能仅填写“已完成”。

**4. 工作包详细执行与验收**

**P00｜建立可重复基线**

改动入口：[pyproject.toml](E:/fpga/量化gpt/pyproject.toml)、[CI](E:/fpga/量化gpt/.github/workflows/ci.yml)、[Makefile](E:/fpga/量化gpt/Makefile)、[测试说明](E:/fpga/量化gpt/docs/TESTING.md)。建立 Python 3.12 隔离环境和分平台依赖锁，约束 MCP 兼容主版本；加入 Windows PowerShell 安装/启动命令。修正 Windows 路径断言及已有 Ruff import 问题。

验收：Windows/Linux 全新安装都能导入 MCP/API；现有测试在基线环境通过；前端构建通过。Python 通道允许 Rust 明确禁用并验证回退；Rust 启用通道须在受支持的 Python/PyO3 组合中执行测试。重跑 Pyright 后按实际基线处置，不把上一轮 Python 3.14 的 436 项诊断直接当修复数量，也不关闭整类检查。若仍有历史诊断，逐项固定 file/rule/message 基线，新增诊断为零，改动模块的真实错误清零，G4 前消除未解释诊断。先建立性能 harness，记录原基线的运行时间/读取量/计算次数，但明确其数值结果含已知错误，不能作为新算法的正确性 oracle。

**P01｜先提交契约和黄金样例**

改动入口：[adapter 契约](E:/fpga/量化gpt/quantgpt/strategy/adapters.py)、[StrategySpec](E:/fpga/量化gpt/quantgpt/strategy/spec.py)、[表达式解析器](E:/fpga/量化gpt/quantgpt/expression_parser.py)、[schema 文档](E:/fpga/量化gpt/docs/STRATEGY_SPEC.md)。形成字段单位/可用时刻、算子语义、事件顺序、标识/版本、错误码和状态转换的契约表及 JSON fixtures。将行业/市值中性化设为明确依赖，缺真实字段返回 capability blocker。

验收：每个后续负责人用同一组 fixtures；有 schema 兼容/拒绝用例；交易日历接口明确，内部模拟参数不会被旧版本禁用字段检查误拒或绕过；scale、rank、ties、warmup 的目标结果写入 golden cases。冻结 signal_ref、evaluation_id、strategy_run_id 的 DTO 和权限解析规则，供 P08/P10/P17/P18 共用。事件顺序用 golden case 固定为上一收盘持仓、公司行动/权益确认、当日开盘成交、收盘估值；新开盘仓位不取得成交前隔夜收益或此前分红权益，现金费用与基准再投资口径单独说明。

**P02｜修算子和行身份（C01–C05）**

改动入口：[expression_parser](E:/fpga/量化gpt/quantgpt/expression_parser.py)、[neutralize](E:/fpga/量化gpt/quantgpt/neutralize.py)。逐个修 scale 的全样本读取、BOLL 跨股计算、中性化 `.values` 错位；禁止 cap/市值中性化静默退化为 close 或 close×volume。统一 `(security_id, session)` 行身份和逐股/逐日计算原语。

验收：追加未来数据不改变过去因子；输入行顺序变化不改变按主键对齐的结果；不同股票互不污染；ties/NaN/常数/零/无穷/短窗口有明确输出；不存在同名经济变量的隐藏代理。修复结果进入真正 pytest 回归，审阅脚本保留为历史复现记录。

**P03｜修数据资格、语义和快照（C06、C09、C11）**

改动入口：[data_quality](E:/fpga/量化gpt/quantgpt/data_quality.py)、[fundamental_data](E:/fpga/量化gpt/quantgpt/fundamental_data.py)、[data_snapshots](E:/fpga/量化gpt/quantgpt/data_snapshots.py)、[market_data](E:/fpga/量化gpt/quantgpt/market_data.py)。把全样本质量报告与交易资格分开；资格只能引用决策时刻已知的历史。删除财务字段错误等价映射；区分缺数据/经济代理。先实现通用有效期和 vintage 容器，为 P13/P14 填充真实美股数据。信号表与估值表独立。快照保存实际使用的全部字段、有效期、vintage 和文件内容哈希，财务 enrich 后完成最终 manifest。

验收：未来退市或缺数不反向删除训练股票；仅改变 suspended/industry/shares/财务数值也改变相关快照；同一快照可重新加载并验证内容；更新上游缓存不改变旧快照；供应商缺等价字段有结构化错误。缓存检查依据 sessions 及内部缺洞，原子写入、并发补数去重，不直接拼接不同复权基准。

**P04｜重建持仓核算（C07、C08、C11、C12、C13）**

改动入口：[因子回测](E:/fpga/量化gpt/quantgpt/backtest.py)、[策略回测](E:/fpga/量化gpt/quantgpt/strategy/backtest.py)、[风险规则](E:/fpga/量化gpt/quantgpt/strategy/risk.py)。抽取可复用的研究账本，保存数量、现金、价格、NAV、实际交易金额和费用；无调仓时数量不变。费用按买卖金额计提，处理首笔建仓，明确多空研究收益的 gross exposure 归一化和两侧费用。以唯一交易会话序列生成调仓日期，切窗只截取该序列。因子分组若采用每天等权再平衡，也必须明确并计成本。

验收：A/B 各半且 A `100→200→100` 的持有收益为 0%；恒价资产换仓后只能损失费用；固定交易路径提高费用不能改善净收益；初始现金、权重上限和成本约束下不能产生意外负现金；首日亏损计入回撤；停牌/缺价/退市事件不能静默记零。拆股和分红用合成事件测试数量/NAV 守恒，P13 再以真实样例核对。区分除息日应收与支付日现金；除息日开盘新买入不取得此前分红权益；采用 raw 价格加现金/应收账本时不得再重复叠加价格 total-return 调整。记录现金并购、换股、分拆及终值未知事件的支持或阻断政策。

**P05｜统一信号与指标（C10、C13–C15）**

改动入口：[strategy/signals](E:/fpga/量化gpt/quantgpt/strategy/signals.py)、[strategy/backtest](E:/fpga/量化gpt/quantgpt/strategy/backtest.py)、[validation/split](E:/fpga/量化gpt/quantgpt/validation/split.py)、[rolling_validator](E:/fpga/量化gpt/quantgpt/rolling_validator.py)。保留缺失 mask、最少有效因子和历史长度；通过 AST 计算嵌套 lookback；分清组件原始 IC、方向调整 IC 和复合信号 IC。所有 IC horizon 用共同市场会话及一致的未来价格定义。接入 adapter 的 benchmark total-return 序列。

验收：全因子缺失不新建仓；仍持有的资产继续用完整估值表计价；交换因子列表不改变组合信号/IC；在数据、方向、初始数量/现金、warmup、公司行动和首次建仓费用政策均相同的条件下，普通与 OOS 相同窗口的指标一致。fresh_cash/carry_forward 各有 golden case，记录起始持仓/现金、状态来源及边界费用，不要求不同初始状态的结果强行相等。warmup 只提供过去特征/估计状态，不进入评分窗收益；有限窗口充分预热后再增加历史不应改变评分结果，EMA 等递归算子单独记录初始化及数值容差。嵌套 20/60 均值具备完整预热；train/valid/test 的标签不跨边界，按实际 horizon purge；基准与策略日期/币种/成本口径可解释。

**P06｜建立双引擎差分测试**

改动入口：[rust_bridge](E:/fpga/量化gpt/quantgpt/rust_bridge.py)、[Rust 求值器](E:/fpga/量化gpt/engine/src/expression/eval.rs)、[Rust tests](E:/fpga/量化gpt/tests/test_rust_bridge.py)。用 P01/P02/P05 相同 fixtures 对照实际编译的两套引擎，修 rank/ts_rank、ties 和窗口语义；用浮点容差比较数值、精确比较分组及缺失 mask。

验收：安装/未安装扩展不会静默改变研究结论；不一致算子确定回退 Python，返回实际 engine_used/version/fallback_reason。G1 可以采用全 Python 路径，但必须关闭未验证的加速路径并通过回退测试；宣称 Rust 可用前必须完成实际编译差分，不能只 mock 长度/类型。

**P07｜统一项目身份与因子池**

改动入口：[models](E:/fpga/量化gpt/quantgpt/models.py)、[factor_pool](E:/fpga/量化gpt/quantgpt/factor_pool.py)、[旧收藏接口](E:/fpga/量化gpt/quantgpt/routes/factor_library.py)、[新池接口](E:/fpga/量化gpt/quantgpt/routes/factor_pool.py)、[auth](E:/fpga/量化gpt/quantgpt/auth.py)。增加项目和成员关系；新旧因子池以 definition + evaluation 关联统一，收藏是视图；同一公式在不同市场/时期可有多个评价。为 MCP 系统身份提供明确的项目关联，不隐式迁移给所有用户。此包同时负责最小网页项目选择、共同因子列表和详情/收藏入口，复用现有 [FactorLibrary](E:/fpga/量化gpt/frontend/src/components/FactorLibrary.tsx) 与 API，满足 G2 网页演示；完整策略/WQ/任务体验留 P17。

验收：同项目 MCP 创建条目网页可见；不同用户/项目不可越权读取；标签/状态/旧收藏/原始 hash/报告链接不丢失；重复迁移幂等；同公式跨市场不会被覆盖。测试伪造 project_id、跨项目 artifact_ref、成员被移除后的访问拒绝；历史系统身份认领要求明确管理权限及审计。任务创建冻结 actor/project，后续任务/产物/缓存读取重新检查当前权限。通过 Alembic 升级、行数/归属核对和回滚演练。

**P08｜统一实验和产物身份**

改动入口：[experiment_ledger](E:/fpga/量化gpt/quantgpt/experiment_ledger.py)、[models](E:/fpga/量化gpt/quantgpt/models.py)、[data_snapshots](E:/fpga/量化gpt/quantgpt/data_snapshots.py)。在现有实验账本上增加 versioned evaluation config、project、backend、数据引用和依赖关系；区分定义、评价、策略三种 hash，旧 factor_hash 仍按原义保存。把因子值、收益、诊断、报告作为有类型的 artifact，接口默认返回引用和摘要。

验收：相同不可变输入获得相同内容身份；窗口、费用、方向、PIT vintage、字段/引擎版本改变会失效相应评价；失败、取消、基础设施重试和研究新试验分开记录；只有已验证完成的产物可成为晋级证据。本地数据无法定位或版本未知时显示 research_only，不能补造 snapshot。WQ 采用独立 remote evidence schema 和平台 scope，其底层版本未知不否认真实平台报告存在，但不能据此授予本地策略晋级/独立终验证明；仅按 WQ 适用 policy 评估平台边界。

**P09｜统一验证和最终测试锁**

改动入口：[validation](E:/fpga/量化gpt/quantgpt/validation/promotion.py)、[policy](E:/fpga/量化gpt/quantgpt/validation/policy.py)、[statistics](E:/fpga/量化gpt/quantgpt/statistics/multiple_testing.py)、[策略验证](E:/fpga/量化gpt/quantgpt/strategy/validation.py)、[搜索账本](E:/fpga/量化gpt/quantgpt/search_ledger.py)。本地可控制 holdout 的因子和策略默认 selection，测试区间仅 final 阶段读取；WQ 只执行其实际能力支持的检查，不把平台 IS 冒充此契约。研究项目登记搜索族、试验口径、所有搜索尝试；已观察绩效并参与选择的失败候选也计入搜索，不能只计成功者；只有相同不可变输入的基础设施重试不增加研究试验，解析/数据失败和取消另记原因。真实 walk-forward 逐窗拟合/选择/冻结；原策略健康检查另标类型。晋级强制绑定数据质量、OOS、滚动/安慰剂等适用检验及试验校正；未知返回 blocker。

验收：final 之前接口、报告和缓存都不泄露测试成绩；冻结 spec/config 后才执行 final，同输入重试复用原结果；final 使用记录绑定研究族、因子/策略血缘及数据/时间窗口，不只绑定 evaluation_hash，预算和解锁在项目范围内以事务更新。更换 evaluation ID、strategy_hash 或在同一研究血缘下派生候选不能消除已暴露记录；相关新候选只能 exploratory 复评该窗口，不能再用它取得独立终验/可信晋级，需预登记未暴露窗口或保持 research_only。已观察选择指标后取消的尝试仍计入搜索。统计量/试验次数从服务端账本和序列生成，不能手填通过状态；无次数不得默认为一次。用“多个失败候选后挑中成功者，次数不为 1”和“换 ID 不能重置 holdout”做负例。先实现经验证的 FDR 和带自相关处理的置信区间，再按适用性实现 PSR/DSR。门禁判断错误与不具备统计显著性是不同状态。

P09 同时交付版本化 validation_profile，明确 required/optional/not_applicable；required 未运行或失败必须阻断，可选方法 unavailable 不默认为通过，也不误阻断未要求该方法的 profile：

| profile | 必需证据类别 | 可授权范围及限制 |
|---|---|---|
| local_factor | 定义/配置绑定、可重放快照、DQ、方向及 OOS、标签边界、滚动/适用安慰剂、试验校正和冗余检查 | 本地因子候选；不代替完整策略证明 |
| local_strategy | 完整 spec/hash/血缘、数据及模拟口径、策略自身 OOS/walk-forward、费用/风险、试验校正 | 该策略候选和信号导出；不能借用单因子通过标志 |
| wq_remote | 请求设置/remote_run_ref/观测时间/原始响应、平台必需检查、适用 scope 与现有提交门禁结果 | 对应平台工作流；正式提交仍要求现有 preflight 或合规记录的显式 override，不授权本地策略导出 |

各 profile 在 P01/P09 定义可选 PSR/DSR、序列依赖和不适用原因；WQ 取不到原始序列时不强行运行本地统计。测试同时证明合法 WQ 证据可按自身 policy 处理，以及同一证据不能通过 local_strategy 边界。

**P10｜策略版本、血缘与导出闭环**

改动入口：[strategy/spec](E:/fpga/量化gpt/quantgpt/strategy/spec.py)、[strategy/service](E:/fpga/量化gpt/quantgpt/strategy/service.py)、[strategy/export](E:/fpga/量化gpt/quantgpt/strategy/export.py)、[策略 REST](E:/fpga/量化gpt/quantgpt/routes/strategy.py)。建立完整策略实验，引用因子定义/评价版本，并保存策略自己的验证证据。v2 承载明确模拟与验证契约，v0/v1 通过兼容适配读取，其旧实验不自动获得新证明。导出通过 strategy_run_id 查询服务端证据，再产生 versioned candidate artifact。

验收：使用真实业务 service、测试数据库和确定性行情完成“因子入池→建策略→selection→final→晋级→导出”，不 mock 掉待验闭环、不手工注入 provenance。错策略/错项目/错快照的证明拒绝；成本或权重改变后旧证明失效；策略拒绝也能正常出报告；新导出具备 strategy_hash、因子血缘、as_of/data_end、模拟口径和适用范围。

**P11｜统一任务状态机**

改动入口：[task_store](E:/fpga/量化gpt/quantgpt/task_store.py)、[task_executor](E:/fpga/量化gpt/quantgpt/task_executor.py)、[mcp_task_helper](E:/fpga/量化gpt/quantgpt/mcp_task_helper.py)、[backtest_tasks](E:/fpga/量化gpt/quantgpt/routes/backtest_tasks.py)、[策略任务](E:/fpga/量化gpt/quantgpt/routes/strategy.py)。公共 submit/status/cancel/result 服务同时供 REST/MCP 使用；任务记录与待派发事件在同一事务写入，以 outbox 或等价持久派发消除“已接受却未执行”的崩溃窗口。worker 使用 lease/heartbeat、attempt_id/fencing；结果发布核验当前 attempt、revision 和终态，迟到 worker 不得覆盖新执行。支持 deadline、并发配额、本地幂等键、进度事件与中断恢复。

验收：同幂等键+同 payload 只创建一项本地任务，同键不同 payload 明确冲突；运行中取消不会再变 completed；进程重启后状态可查，失去租约的本地执行变 interrupted/retryable。故障注入覆盖落库后派发前崩溃、重复派发、worker 失联、旧 worker 迟到、取消与完成竞争，任务不丢失且产物不被覆盖。SSE 能推同阶段进度及恢复 cursor；cursor 超出保留期时返回最新快照并重新订阅。缓存、provider 等待、CPU 阶段均有取消检查，不能中断的调用注明上限/等待原因；任务和产物查询检查项目权限。基础设施重试不会污染研究试验次数。远程任务失联不直接推定可重发，遵循 P12 的结果不确定状态。

**P12｜WQ 作为研究执行后端接入**

改动入口：[wq_brain_service](E:/fpga/量化gpt/quantgpt/wq_brain_service.py)、[wq_brain_client](E:/fpga/量化gpt/quantgpt/wq_brain_client.py)、[wq_submission_guard](E:/fpga/量化gpt/quantgpt/wq_submission_guard.py)、[WQ routes](E:/fpga/量化gpt/quantgpt/routes/wq_brain.py)。保存 WQ expression、region/universe/delay/decay/truncation/neutralization、simulation/alpha ID、原始平台结果及观测时间到公共实验。新增 capability 差异报告和原始字段/算子映射表，远程专属字段明确标识。

验收：同一研究项目可并排查看本地与远程证据，但不合并成一个无来源分数；无法取得平台底层数据版本时如实记录未知，使用 remote_run_ref 而非伪造本地快照；A 股或不同 universe 的本地证据不能授权 USA 目标。保留现有显式 override 审计规则，不把 override 当自动通过。远端接受 POST 后响应丢失时，进入 remote_outcome_unknown/reconciliation_required：保存请求指纹和已有 remote ID，优先查询/对账；平台无对账或幂等能力时不得自动盲重发。已有 remote_run_ref 的恢复走轮询，不重新创建。取消分别记录 cancel_requested、local_wait_cancelled、remote_cancel_confirmed，只有平台明确确认才显示远端已取消。mock 覆盖这些不确定结果及认证/限流/检查失败；真实平台模拟属于凭据具备后的 external_integration 验收，实际提交不是发布测试。

**P13｜接入真实美股行情与证券主表**

改动入口：[market_data_providers](E:/fpga/量化gpt/quantgpt/market_data_providers.py)、[adapter 注册](E:/fpga/量化gpt/quantgpt/strategy/adapters.py)、[market_data](E:/fpga/量化gpt/quantgpt/market_data.py)、[因子 MCP](E:/fpga/量化gpt/quantgpt/mcp_server.py)、[factor_values](E:/fpga/量化gpt/quantgpt/factor_values.py)。先完成 provider 能力/许可决策记录，再实现一个真实日频 US provider，证券主表、纽约会话/夏令时、公司行动和历史 universe 资格；原始价格、因子价格与估值/收益口径分离。因子与策略的 REST/MCP 数据路径都按 market/backend 经公共 adapter 分派，消除因子入口继续硬编码 A 股的断点；缺字段在执行前给 capability report。固定 feed/adjustment/asof、分页和数据版本；行情缺口按交易会话增量补齐。合成 demo 单独保留并明显标识。

验收：稳定证券 ID 能贯穿改名；拆股、分红、停牌、退市、节假日/半日市/夏令时与缺洞有 fixtures 和真实样例；公司行动不双重计入；历史成分可按日期重建；不以当前 ticker 列表冒充无幸存者偏差股票池。真实验收分别要求：改名身份连续性、至少一个退市或现金并购退出的终值结算、至少一个拆股、至少一个现金分红，不能用改名样例代替退市。换股/分拆/终值未知按支持矩阵处理，不支持时阻断受影响研究的可信验收，不能追溯删除该股票历史。通过美股因子回测/截面值和策略回测的 REST/MCP 用例，证明不只是策略 adapter 可用。

**P14｜接入美股 PIT 财务与风险基础字段**

改动入口：[fundamental_data](E:/fpga/量化gpt/quantgpt/fundamental_data.py)、[DataField 契约](E:/fpga/量化gpt/quantgpt/strategy/adapters.py)、[data_snapshots](E:/fpga/量化gpt/quantgpt/data_snapshots.py)。实现选定来源的股数、市值、行业和核心报表字段，保存币种/单位、财报周期、公开/可用时刻、修订、抓取时间；季度/年度/TTM 避免重复累计；处理公告缺时刻时的保守可用规则并在契约中写明。

验收：盘后公告不能用于同日已发生的成交；后续修订不改变旧时点实验；shares 与价格的拆股口径一致；来源切换不改变字段经济含义；同一财报样本的派生指标能手算核对。价格和财务两类美股因子都可在真实冻结样本上形成完整研究记录。

**P15｜完整因子研究卡**

改动入口：[factor_values](E:/fpga/量化gpt/quantgpt/factor_values.py)、[attribution](E:/fpga/量化gpt/quantgpt/attribution.py)、[factor_similarity](E:/fpga/量化gpt/quantgpt/statistics/factor_similarity.py)、[report](E:/fpga/量化gpt/quantgpt/report.py)。在固定数据/费用/窗口下产出经济假设、字段/覆盖、IC horizon/衰减/区间、分组毛净表现、换手/费用敏感性、风险暴露、历史稳定性、与库内信号及收益相关性、组合增量贡献、统计与适用 scope。先用动量、反转、波动/流动性及基本面代表样例贯穿流程，表达式在 selection 之前登记。

验收：全部图表/指标可追溯至同一评价；factor similarity 不止比较字符串；增量贡献相对预先冻结的组合基线，在同一 universe/成本/split 下评估；多 horizon/参数尝试计入研究预算，在 final 上查看差异同样消耗该留出窗口。空数据、不显著、不可执行都有明确结论；报告说明本地/平台来源和当前可用字段限制，不给综合评分伪装成通过证明。

**P16｜组合约束与优化**

改动入口：[strategy/portfolio](E:/fpga/量化gpt/quantgpt/strategy/portfolio.py)、[strategy/risk](E:/fpga/量化gpt/quantgpt/strategy/risk.py)、[strategy/optimizer](E:/fpga/量化gpt/quantgpt/strategy/optimizer.py)、[strategy/score](E:/fpga/量化gpt/quantgpt/strategy/score.py)。先建立去重复信号后的等权/收缩基线，接入行业、size/beta、个股权重、现金、ADV/换手约束。再加入风险协方差与显式成本目标，训练/验证区间选参数，输出可行性和约束残差；现有归一化权重功能单独命名，避免暗示已做风险优化。

验收：不可行约束返回解释而非静默放松；协方差/预测只来自过去；优化失败可回退已记录的基线；相同 split 下报告与基线差异及成本敏感性。优化器不以“收益一定更高”验收；算法正确、约束可证、样本外增量如实报告即可。借券/融资/short 扩展列入后续独立包，不默认开放。

**P17｜工作台与操作闭环**

改动入口：[StrategyWorkbench](E:/fpga/量化gpt/frontend/src/components/StrategyWorkbench.tsx)、[StrategyDiagnosticsPanel](E:/fpga/量化gpt/frontend/src/components/strategy/StrategyDiagnosticsPanel.tsx)、[因子库 API](E:/fpga/量化gpt/frontend/src/api/factorLibrary.ts)、[TaskCenter](E:/fpga/量化gpt/frontend/src/components/tasks/TaskCenter.tsx)、[WQ 工作区](E:/fpga/量化gpt/frontend/src/components/wq/WQBrainWorkspace.tsx)。研究上下文可编辑市场/股票池/日期/基准/成本；按 capabilities 加载字段和可用性；因子池支持带版本加入策略；优化传 signal_ref，导出传策略运行 ID；展示阻断原因与所需步骤。统一登录刷新客户端。

验收：真实应用服务配固定样本完成网页端到端用例，覆盖重新登录、MCP 因子可见、编辑日期、组合构建、优化、selection/final、被拒/通过、导出、取消及刷新后恢复；没有通过手工 JSON 补证据。截图/录像及测试结果留档；JSON 为高级视图，主要界面清楚展示净收益/基准/回撤/持仓/成本/数据来源。

可提前合入的低耦合修复：日期/基准输入、字段 discovery 去硬编码、优化请求 score 契约、统一 auth 客户端；最终端到端验收仍依赖 P10/P11/P13。

**P18｜统一评价工具和批量缓存**

改动入口：[mcp_server](E:/fpga/量化gpt/quantgpt/mcp_server.py)、[task_executor](E:/fpga/量化gpt/quantgpt/task_executor.py)、[factor_miner](E:/fpga/量化gpt/scripts/factor_miner.py)、[strategy/service](E:/fpga/量化gpt/quantgpt/strategy/service.py)。新增计划/提交评价与读取产物的业务 service，旧工具作为兼容包装器，保留已声明的请求参数含义；计算错误修复按新语义/引擎版本执行，不保留错误费用或前视逻辑。一个 batch 共享一次数据面板、表达式编译和公共子计算；报告按需生成。缓存包含定义/配置/数据/引擎版本及输出类型；列表使用 DB 分页和精简摘要。

验收：score/diagnose/report 对同一 evaluation 不重读全行情或重做回测；新旧入口在相同已修正语义版本下结果一致；不同方向/OOS/费用不能错误命中；并发请求不会重复补同一缺口；缓存条目遵守项目访问控制；本地幂等重试不重复创建任务，远程结果未知时按 P12 对账，不承诺平台未提供的 exactly-once。返回 retryable/error_code/next_action。默认工具返回小摘要与 artifact_ref，大数组按需读取。

**P19｜性能与加速验收**

使用 P00 创建的 harness，在 G1 通过后、P18 性能重构前保存“正确性已修复”的性能基线，作为主要比较对象。在同一硬件、依赖和不可变数据上建立两组基准：可在 CI 运行的合成小样本；本地 500 股×10 年日线×20 表达式的冻结研究样本。后者须有 P13/P14 数据覆盖及许可，未具备时明确真实基准待验，不能用合成数据冒充。记录冷/暖缓存耗时、p50/p95、峰值 RSS、provider 请求/读取字节、序列化耗时、重复计算次数和摘要体积，保存改造前后结果。

先验收结构性目标：同一 batch 只构建一个共享面板；重复读取既有评分不触发 provider 或引擎；摘要默认不携带全量收益/持仓；超预算任务有背压；取消检查可观测。随后根据基准设定性能预算及允许波动范围，门禁要求无显著退化。Rust 仅优化差分通过且 profile 证明值得优化的核，不能用关检查/改算法含义换速度。

**P20｜发布与文档收口**

更新 [README](E:/fpga/量化gpt/README.md)、[架构](E:/fpga/量化gpt/docs/ARCHITECTURE.md)、[API](E:/fpga/量化gpt/docs/API_DOC.md)、[MCP](E:/fpga/量化gpt/docs/MCP_GUIDE.md)、[策略契约](E:/fpga/量化gpt/docs/STRATEGY_SPEC.md)、[导出契约](E:/fpga/量化gpt/docs/strategy_signal_export.md)、[数据源](E:/fpga/量化gpt/docs/market_data_sources.md)、[Agent 工作流](E:/fpga/量化gpt/docs/agent_safe_workflow.md)。明确支持矩阵、真实/演示数据、工具能力、旧结果兼容和迁移路径。

验收：Windows/Linux 安装和 CI 必需检查通过；运行 A 股回归、美股真实冻结样本端到端、WQ mock 端到端、权限/迁移/重启/取消/缓存失效回归；完成发布清单、复算影响说明和回滚演练。未具备真实数据或凭据的能力独立标识，不能用 mock 成功冒充外部集成验收。

**5. 阶段退出门禁与并行安排**

| 门禁 | 可演示的结果 | 通过条件 |
|---|---|---|
| G0 环境与契约 | 新开发环境可运行，接口/语义 fixtures 固定 | P00/P01 verified；干净安装和基线测试可重现 |
| G1 可信本地计算 | 合成/现有 A 股样本能正确计算因子和持仓 | P02–P06 verified；审阅 C01–C15 全部有归属和对应回归；Rust 不可用时确定回退且不宣称加速已验证 |
| G2 共享闭环 | 同项目 MCP 与网页共用因子池，合法形成策略证据与导出 | P07–P11 verified；实际 service 的正/负端到端路径通过；final 锁与权限隔离通过 |
| G3 美股研究与 WQ 接口就绪 | 本地真实美股价量/财务研究和 WQ 记录共用研究项目 | P12–P15 contract_verified，P13/P14 external_integration=verified；WQ mock 通过但无真实验收时只标接口就绪；只有 P12 external_integration=verified 才宣称真实双路线集成 |
| G4 可交付工作台 | 组合研究、任务恢复、批量复用和前端全流程稳定 | P16–P20 verified；性能、迁移、文档和平台回归完成；所有能力标识与实际验收一致 |

各发布门禁累积生效：G2 需先通过 G1，G3 需通过 G2，G4 需通过 G3；任务可提前开发，但不能绕过前置门禁对外宣称阶段完成。建议批次：第一批 P00/P01；第二批 P02/P03/P04 与 P07 并行，前端交付 P07 最小共池入口及低耦合输入修复；第三批 P05/P08/P11 并行，随后 P06/P09/P10；第四批 P12 与 P13 并行，随后 P14/P15；第五批 P16 与 P18 并行，P17 可按已冻结 DTO 先开发，在 P15/P16/P18 完成后集成验收，P19 随基准依赖就绪执行，最后 P20。按门禁推进，任务开始时再基于拆分后的变更和数据条件估算工期。

集成负责人统一管理 `models.py`、migration、StrategySpec 和公共 DTO，避免多个代理同时改 schema；计算负责人统一合入 `backtest.py/strategy/backtest.py`；数据负责人统一合入 `fundamental_data.py/market_data.py`；工具改造 `mcp_server.py` 由服务负责人收口。其他代理可并行写独立测试、适配器或前端组件，并做交叉只读评审。

**6. 审阅发现追踪表**

| 原审阅发现 | 负责工作包 | 必须留下的证据 |
|---|---|---|
| C01 scale 前视 | P01、P02 | 未来追加不变性回归 |
| C02 中性化错位 | P02 | 按主键重排后相同值 |
| C03 BOLL 跨股 | P02 | 多股票隔离 golden case |
| C04 Rust 语义差异 | P06 | 真实双引擎逐值对照/确定回退 |
| C05 市值代理错误 | P01、P02、P03 | 缺真实字段失败及单位测试 |
| C06 财务映射错误 | P03、P14 | 逐字段等价契约和财报手算 |
| C07 隐含每日再平衡 | P04 | 持股/现金/NAV 守恒 |
| C08 多空费用抵消 | P04 | 恒价换仓净收益严格扣双边费 |
| C09 全样本筛选泄漏 | P03、P09 | 改测试期不改变训练资格 |
| C10 全缺失仍买入 | P05 | 空信号不新建仓 |
| C11 缺价/信号删行 | P03、P04、P05 | 独立估值、退市/停牌/缺价政策 |
| C12 回撤和初始费用 | P04 | 初始净值与首笔收费 |
| C13 调仓锚点 | P04、P05、P13 | 统一 session 序列截窗一致 |
| C14 多因子 IC 口径 | P05 | 因子换序、普通/OOS 一致 |
| C15 嵌套预热 | P02、P05 | AST 依赖与完整窗口 |
| 数据快照/PIT/历史股票池 | P03、P08、P13、P14 | 改数失效、历史版本可重放 |
| final 泄漏/多重试验/证据移用 | P08、P09、P10、P12 | 服务端来源绑定和负例 |
| 日期/基准/优化按钮/导出断点 | P10、P17 | 真实 service 的 UI 端到端 |
| 因子池与身份隔离 | P07、P17 | 同项目互通、跨项目拒绝 |
| 取消/重启/SSE/重复运行 | P11、P18 | 故障注入和幂等验证 |
| 重复计算/大返回体/加速 | P18、P19 | 计算次数、缓存命中和基准 |
| 安装/Windows/Pyright 问题 | P00、P20 | 支持矩阵与干净 CI |

**7. 迁移、旧结果与回滚**

采用扩展字段/新表 → 回填及核对 → 新入口切换 → 兼容读取的顺序。第一版选择短维护窗口：暂停新提交并排空/记录在途任务，旧写入口和 worker 完成写入均受维护开关约束，回填/核对完成后一次切换写路径；不在持续双写未设计完整时边回填边切换。保留原始 payload、旧 hash 和原所有者；无法确定 MCP 历史数据归属时进入待关联区，只对原授权身份可见。不得批量归入公共项目。迁移有 dry-run 报告、备份与可重复执行检查；测试维护开始前最后一笔和恢复后第一笔收藏、任务、实验及归属均不丢失、不重复。

修复算法后旧净值和评分不再被视为新版本可信证据，标记 `legacy_unverified/recompute_required` 并保留报告；不得批量自动晋级或覆盖旧成绩。复算创建新的 evaluation ID，以 supersedes_evaluation_id 关联旧记录，分别保留配置、数据、算子和引擎版本。发布前至少对固定 A 股回归样本、美股价量/财务样本复算并给出差异原因。

优先在扩展 schema 上回滚应用入口/feature flag，保留升级后的新用户、任务、实验和 artifact。只有确认无新增写入，或已完整保全且演练过重放增量时才恢复升级前数据库备份；不能用恢复备份丢弃新实验。旧应用通过兼容只读入口读取新版本对象，后端也禁止它们的晋级/导出，不能仅隐藏 UI 按钮。回滚演练须核对升级后新写入仍完整可查。坏缓存按 manifest/version 失效，不直接清空用户研究资产；取消或中断任务不伪装成因子失败。

**8. 测试与完成定义**

每个工作包先提交能暴露该缺陷的回归/契约样例，再实现修复，最后做相邻模块与集成检查。算法用数值/经济不变量，接口用实际 service 和测试数据库，外部 provider 用固定响应；不能用所有层都 mock 的测试证明闭环。

常用 Windows 命令（在 P00 建好的项目环境执行）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --tb=short
.\.venv\Scripts\python.exe -m ruff check quantgpt tests
.\.venv\Scripts\python.exe -m pyright --pythonpath .\.venv\Scripts\python.exe quantgpt
npm.cmd --prefix frontend run build
git diff --check
```

涉及 Rust 时在引擎目录运行 `cargo check --all-targets` 和 `cargo test`，并运行已构建扩展的 Python 差分测试；涉及前端流程时运行 P17 新增的浏览器端到端用例；涉及数据库时验证 SQLite 和 PostgreSQL 迁移及项目权限。每个阶段通过一次完整回归，日常按改动范围执行必要检查。

工作包 `verified` 需要：改动说明、问题到测试的映射、必要检查结果、兼容/迁移说明、产物引用、独立审核结论。未运行的外部验收单列；任何模拟或样例绩效都注明来源。

**9. 外部依赖与可独立推进的工作**

| 依赖 | 在哪个节点确定 | 缺失时仍可进行什么 | 不能据此宣称完成什么 |
|---|---|---|---|
| 美股数据来源、预算、许可与凭据 | P13 实际接入/取真实样本前 | provider 接口、缓存/PIT/事件 fixtures、模拟/权限/界面工作 | G3 真实美股数据验收 |
| 历史成员/退市/公司行动覆盖 | P13 数据验收 | 固定 cohort 明确标记的探索测试 | 动态无幸存者偏差 universe |
| 财务 vintage/公开时间/股数 | P14 验收 | 核心 schema、合成 PIT 测试、价量研究 | 完整基本面因子研究 |
| WQ 凭据与可用权限 | P12 真实连接验收前 | mock 契约、结果归档、scope 检查、界面 | 真实平台模拟成功或平台提交资格 |
| 性能机器和真实冻结样本 | P19 实测前 | 合成基准和结构性重复计算检查 | 真实工作负载加速倍数 |

数据供应商决策记录比较：历史覆盖、退市/公司行动、PIT 与修订、单位/时区、请求限制、费用和许可、离线快照能否保留。本计划不预先锁定付费采购；工程设计先保持一个主 provider 与明确失败路径，避免第一阶段同时接入多家不等价来源。

**10. 第一次实施启动清单**

1. 记录当前 git 状态，保留现有审阅材料和用户变更；选择当前合适 checkout，在需要并行写入时再建立隔离分支/工作区。
2. 执行 P00，确认支持矩阵、依赖锁和基线诊断；不在当前 Python 3.14 环境下跳过兼容报错充当成功。
3. 完成 P01 fixtures 和职责分配，锁定算子、事件时序与研究身份接口。
4. 并行推进 P02/P03/P04/P07；各代理交付自己的测试和变更，集成负责人统一收口 schema 与共享文件。
5. 通过 G1 后复算固定样例，确认旧成绩标记和误差说明，再推进共享策略证据闭环及真实美股接入。

**11. 写后审核记录**

初稿完成后，主代理与三个专项只读审阅者进行了同一轮写后审核，分别检查计算/验证、数据/平台边界、工具/迁移/体验。已将阶段依赖与最小网页归属、holdout 与试验计数、后端验证 profile、初始持仓、真实事件样例、远程结果不确定、持久派发、身份权限、迁移写入与回滚保数、正确性能基线等修订落实到 v1.1。

审阅 C01–C15 全部映射到责任包；21 个工作包的依赖、表格和本地链接经机械核对。详细问题和处理记录见 [写后审核记录](E:/fpga/量化gpt/docs/reviews/2026-10-05-execution-plan-self-review.md)。计划已审核并可作为分包实施基线；全部产品工作包仍为 planned，外部数据/凭据等依赖仍按第 9 节管理。
