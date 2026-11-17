# 美股研究计划执行状态

本轮按用户最新指示优先完成离线契约、免费来源简单验证及付费接口。完整计划 P00–P20 与累积门禁 G0–G4 未整体宣布完成。实现、离线验证、真实接入、研究候选是否通过分别记录。

## 已实施范围

| 包 | 当前状态 | 离线交付与限制 |
|---|---|---|
| P00 | in_review | Python 3.12 分平台哈希锁、Windows脚本、CI、精确历史类型基线；Windows已执行，Linux CI 未在本机执行 |
| P01 | contract_verified | 版本化字段、模拟、数据/评价/策略身份、黄金样例；提交 `a8800a5` |
| P02 | contract_verified | 行身份、逐日scale、逐股BOLL、真实中性化依赖、缺失语义；提交 `6d7a48a` |
| P03 | contract_verified | 历史资格、完整不可变快照/元数据核验、PIT vintage、缓存锁/缺洞/复权基准；提交 `9fcadaa`、`bc18811` |
| P04 | contract_verified | 数量/现金/NAV账本、下一会话开盘、双边费用、公司行动合成守恒；提交 `2e9b215` |
| P05 | contract_verified | warmup、IC/OOS同窗、最少历史、缺信号仍估值、真实benchmark缺失阻断；IC预热期泄漏回归，提交 `31667de` |
| P06 | contract_verified（回退） | Python确定回退；真实Rust扩展已编译测试，但未宣称算子差分/加速全面通过 |
| P07 | in_review | 项目/成员、MCP与网页共池、显式旧收藏迁移、真实网页演示；SQLite升级已演练 |
| P08 | in_review | 复用实验表、不可变hash、授权artifact、紧凑摘要、可重放输入；批量重复请求复用 |
| P09 | partial | 项目最终窗口锁、selection隐藏test、server profiles、实际观察次数与块bootstrap；完整本地独立验证证据尚未齐备 |
| P10 | in_review | v2完整策略血缘与保存信号、按server run ID导出；旧调用者自填证明已拒绝，缺验证时阻断 |
| P11 | contract_verified | 持久接受/outbox、claim/lease/attempt、取消/重启/幂等/撤权及发布保护测试 |
| P12 | contract_verified / external not_run | WQ单次POST、远端引用、仅GET恢复、unknown/cancel语义、平台scope、mock；没有真实模拟或正式提交 |
| P13 | partial / limited_demo_verified | USDataProvider付费接入口、XNYS日历、免费OHLCV；IBM公开demo短样本有真实响应证据 |
| P14 | partial / external not_run | SEC companyfacts/vintage/单位/可用时间fixtures；未配真实联系User-Agent，缺完整股数/行业/退市历史 |
| P15 | partial | 可追溯研究卡、IC区间/毛净分组、假设/基线预登记、数值比较原语；组合增量与真实PIT风险证据仍不足 |
| P16 | in_review | 信号引用优化、过去协方差、约束残差/不可行解释、基线回退、训练/验证参数选择和费用敏感性；不是导出证明 |
| P17 | partial | 登录刷新、项目/共池/评价/v2run/图表/阻断的真实浏览器闭环；完整生产故障矩阵、真实WQ/UI final通过路径未验收 |
| P18 | partial | 统一HTTP/MCP service、持久研究任务、单面板batch、不可变结果复用；跨不同表达式的公共子计算复用尚未实施 |
| P19 | in_review | 合成固定样本服务基准与真实计数；500股×10年×20表达式授权真实数据未具备 |
| P20 | partial | 本轮文档/SQLite迁移/Windows回归；Linux、PostgreSQL与完整真实美股价格+财务验收待执行 |

`contract_verified` 是对应离线契约/回归的结论，并不等同于工作包全部外部验收或发布门禁 `verified`。当前仍有已逐项登记的历史Pyright诊断；新增诊断必须为零，没有扩大基线来掩盖新增错误。

## 证据

- 第一轮全量后端：867 passed、1 skipped（真实扩展安装后“不安装Rust”用例不适用）、17历史warnings。其后修复及新增测试的最终结果见本文件末尾验收记录。
- 数值与数据：见 [持仓账本](RESEARCH_LEDGER.md)、[数据完整性](testing/P03_DATA_INTEGRITY.md)、[研究契约](RESEARCH_CONTRACTS.md)。
- 免费样本：见 [来源决策](US_FREE_DATA_DECISION.md) 与 [实际响应摘要](testing/us-free-external-sample.json)；只验证短期OHLCV接入，不构成策略或生存偏差验证。
- 网页：见 [网页验收](RESEARCH_WEB_WORKFLOW.md)。截图、trace与测试数据库在忽略的 `test-results/`，源码fixture可重建。
- 迁移：见 [迁移步骤与回退边界](RESEARCH_MIGRATION.md)。未修改用户现有数据库。
- API/MCP：见 [新研究接口](RESEARCH_API.md)；[研究卡](FACTOR_RESEARCH_CARDS.md)；[任务、WQ与优化契约](RESEARCH_TASKS_WQ_PORTFOLIO.md)。
- 缓存与性能：见 [服务基准及测试边界](testing/P19_SERVICE_BENCHMARK.md)，区分修复前的三轮性能记录与最终结构复核。

最后一轮交叉审查额外修复：selection 在最终窗口之后读取其预热数据的泄漏、旧策略路由绕过成员撤权、调用者保存记录伪装为服务端策略运行、失效 attempt 污染新 worker 内存状态、依赖版本变化后旧评价缓存误复用。对应回归纳入最终全量测试。任务结果发布在数据库条件更新取得的行锁下完成，取消/失效 worker 不能越过该保护。

## 仍需后续条件

免费compact行情不提供完整历史公司行动、证券主数据/成员资格、退市终值和可验证total-return基准。缺少这些时返回能力限制，不能把样本收益解释为完整美股历史研究。付费供应商可实现 `USDataProvider` 并提供许可、覆盖、版本及完整事件数据后替换；SEC真实请求需配置可联系的User-Agent。WQ真实连接仍需其独立认证和既有提交门禁，本轮只跑mock，不触发正式提交。

旧A股数据若明确是qfq/hfq，raw价格现金账本会拒绝直接使用，避免重复记分红；需要提供一致raw价格及事件数据。旧结果仍可读，新计算采用新语义，不能要求与已知错误历史数值相同。

## 最终验收记录（2026-10-05，Windows / Python 3.12.14）

计算身份为 `sha256:5b9654109f264fe2dbe1087deaf9b0c741230c1ed92d7d44c4d46a4dc09aafa2`，其定义包括实际依赖和平台。后端服务、权限回归、冻结配置、缓存和最终 P19 证据均以该最终源码为准。

- 冻结源码全量后端：**918 passed、1 skipped、17 warnings，341.26秒，覆盖率65.16%**，超过CI的33%门槛。跳过项是已安装真实Rust扩展时不适用的无扩展回退用例；另有独立Python安全回退测试通过。最终日志 `test-results/research-final-frozen-pytest.txt`。
- Ruff：`quantgpt tests scripts/check_pyright_baseline.py scripts/benchmark_research.py` 全部通过。
- Pyright 精确基线门禁：279 项历史诊断、0 项新增、相对初始649项解决370项；研究目录、美股数据目录、研究路由、任务存储/执行/MCP助手与主要数值模块的严格路径诊断为0。未修改历史基线来容纳新问题。
- 浏览器：真实API/SQLite/Edge共3项通过，详见 [网页验收](RESEARCH_WEB_WORKFLOW.md)。MCP应用服务共池经过测试；不把它宣称为MCP网络传输验收。
- 前端：`npm.cmd --prefix frontend run build` 通过；仍有既有大包及动态/静态混合导入提示，不影响构建成功。
- 最后类型收窄后的32项数值/MCP/权限/计算身份回归通过；完整工作流另2项复验通过。上述子集与全量测试重叠，不能相加。
- 最终P19：同批3表达式为1次面板读取/3次计算，重复评价为0次读取/0次计算，9个artifact身份及内容一致，费用变化失效/原结果保留通过。只作为最终结构验证，不从并行CPU负载下的一次测量推导加速结论。
- SQLite 001→017 升级、无新写入回退及新写入后拒绝破坏性回退均通过；未迁移用户现有数据库。

一次中间全量运行因测试期间补类型标注改变源码身份，在重复评价ID断言处失败；冻结源码后的该流程复验通过，随后重新执行整套验收。保留本地日志 `test-results/research-final-pytest.txt` 与最终冻结运行日志以区分两轮结果，未放宽哈希检查或测试断言。

复现主要验收命令（运行期间不要编辑源码或依赖锁）：

```powershell
.venv312/Scripts/python.exe -m pytest tests -q --tb=short -rs --cov=quantgpt --cov-report=term --cov-fail-under=33 -p no:cacheprovider --basetemp data/test-tmp-acceptance-new
.venv312/Scripts/python.exe -m ruff check quantgpt tests scripts/check_pyright_baseline.py scripts/benchmark_research.py
.venv312/Scripts/python.exe scripts/check_pyright_baseline.py --strict-path quantgpt/research --strict-path quantgpt/routes/research.py --strict-path quantgpt/us_data --strict-path quantgpt/task_store.py --strict-path quantgpt/task_executor.py --strict-path quantgpt/mcp_task_helper.py --strict-path quantgpt/strategy/backtest.py --strict-path quantgpt/backtest.py --strict-path quantgpt/expression_parser.py
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run test:e2e
```

运行数据、数据库、报告、截图/trace、coverage、虚拟环境和token文件均通过`.gitignore`排除；源码fixtures与不含原始供应商价格/凭据的验收摘要可提交。所有本轮Git提交仅保存在本地，没有push。

主要阶段提交：环境 `52e052e`、身份契约 `a8800a5`、算子与Python回退 `6d7a48a`、数据 `9fcadaa`/`bc18811`、账本与指标 `2e9b215`/`31667de`、免费US/PIT接口 `80a3937`、共享研究服务/任务/证据 `88a7764`、网页 `208c60f`。文档与P19证据由本验收记录所在提交收口。

## 项目复查修复（2026-10-18）

按本轮复查顺序完成以下改动；用户选择先完成离线验证与数据契约，保留真实数据验收阻断。

| 顺序 | 修复结果 |
|---|---|
| 1 | 因子/策略统一公司行动感知的共同会话收益标签；年化收益使用实际净值复利 |
| 2 | 文件 SQLite 独立事务连接、WAL 和有界锁等待；内存数据库保留共享连接 |
| 3 | 组合/迭代任务接入持久 outbox、幂等和 attempt 校验；迭代冻结父结果、持久保存候选选择；报告在受保护事务中发布；研究数据库协程保持同一 loop，快照及同步计算在线程中执行 |
| 4 | 单因子/组合共用任务跟踪，串行轮询、取消请求、阻止旧会话响应覆盖新状态；历史摘要按需加载详情 |
| 5 | 因子分组复用只读市场上下文；任务 SQL 分页/聚合/摘要投影；认证和会话目录不再附带加载全部任务 |
| 6 | 网页/HTTP/MCP 离线验证任务、冻结输入重放、独立报告、真实数据验收契约；报告并发发布有界重试；事后补 selection 不能取得 OOS 通过 |
| 7 | CI 执行浏览器 E2E 和真实 Rust 扩展差分；修复有限算子子集，未认证范围保持 Python 回退 |

合成性能对照（20只证券、180会话、3600行、5分组，cProfile）：总耗时9.522→4.105秒；五次账本调用累计5.536→0.180秒；市场上下文只准备一次。净值、收益、Rank IC、分组指标、状态和交易内容一致。这是同一合成工作负载的分析器计时，不代表完整真实数据的生产吞吐。

2026-10-18 版离线报告固定返回 `allowed=false` 和 `external_acceptance=not_run`。外部证据现已接入，见文末实测记录。冻结快照完整性不证明历史成分、公司行动覆盖或 total-return 基准已验收；独立截面 placebo、预登记比较库等缺失仍明确显示。Rust 子集通过不自动打开全局语义认证或 metrics 开关。远端 GitHub CI 和 PostgreSQL 未在本轮本机执行。

已执行的分项验证：Rust 单元15项；真实扩展桥接/差分92项通过、1项环境不适用跳过；前端 Node 行为17项、真实浏览器6项、最终离线入口补验1项通过；TypeScript/Vite构建通过。前端既有大包警告仍存在。分项测试包含重叠，不能相加为独立总数。

后端全量回归：**1010 passed、59 skipped、17 warnings，1356.13秒，覆盖率65.97%**，超过CI的33%门槛。59项跳过来自普通Python环境未安装Rust扩展；真实编译扩展已在独立环境完成上述桥接/差分验证。

全量运行期间仍有收尾改动，因此另外完成最终源码的针对性补验：离线报告并发发布1项、研究任务事件循环及相关工作流7项、旧任务与迭代路由14项，全部通过。上述分项有重叠，不与全量结果相加，也不把本轮描述为最终冻结源码的一次性全量验收。最终Ruff通过，当前Linux依赖环境的Pyright检查为0项诊断，`git diff --check`通过；未修改历史类型基线来容纳新增问题。

本机 Linux 沙箱的异步线程唤醒通过测试启动器附加10ms事件循环定时回调，不修改生产事件循环；并发测试使用实际独立 SQLite 连接，数值验证实际重放冻结面板。日志保留在忽略的 `test-results/review-fixes-full-pytest.txt`。

## 2026-11-17 真实多证券及外部证据验收

本次短窗口技术验收在两个独立 SQLite 项目中进行。主代理先登记基线与 4 个方向，每个方向用独立 stdio MCP 连接查重并提交。75 个交易日的选择期为 2025-06-05 至 2025-09-22。按事先固定的最高分组扣费后净收益选择两个方向，保留基线，跳过 5 个交易日后在 2025-09-30 至 2025-12-31 的 65 个交易日终验。两个项目均观察到 4 个同时运行的方向，试验分母各冻结 7 项。

普通本地日频研究的默认规划为选择期 756 个交易日、留出期 252 个交易日。在预热 30 日、隔离 5 日的设置下需要 1,043 个交易日。下表的两份冻结面板各覆盖 752 个交易日，使用默认规划会得到 `insufficient_data`。复现本次短协议时，显式指定 `--selection-sessions 75 --holdout-sessions 65`。

| 固定股票池和冻结清单 | 行情覆盖 | 选择期组合 / 基线净收益 | 留出净收益 | 留出最大回撤 |
|---|---|---|---:|---:|
| Kibot，15 股，`ds_98ac585dc7b96ef10e971080870da4a1` | 752 日，11,280 行 | +3.57% / +5.91% | +2.21% | −11.09% |
| EODHD，5 股，`ds_46885ee79583a73581df39f53945b5b2` | 752 日，3,760 行 | −3.72% / −9.58% | −6.80% | −11.77% |

两组逐行核对了所属供应商的 OHLCV。5 只重叠股票的 36 笔分红匹配。两组留出期的 USD 总回报基准 NASDAQXNDX 均核对了 65 个连续交易日。缺少日线当时的发布时间、已审核的数据使用授权、历史成分与完整退市结算覆盖，故两组外部验收均为 `insufficient_data`，历史成分单项为 `not_run`。两组组合的滚动检验均未通过，多重检验校正后 p 值均为 1，导出均受阻。5 股样本低于计算截面 IC 的 10 股门槛。供应商原始响应、收据、快照和完整报告留在本地 `data/`。[Kibot 数据说明](https://www.kibot.com/api/history-request.html) · [EODHD 数据口径](https://eodhd.com/help/methodology) · [FRED 指数序列](https://fred.stlouisfed.org/series/NASDAQXNDX) · [Nasdaq 指数口径](https://indexes.nasdaq.com/docs/Index_Versions_NDX.pdf)

主样本补入公司行动原始响应的版本摘要后，在原项目中重新验证同一冻结组合。新报告的证据版本为 `f870f034df82aeaa1a90f64a0eae4d4717e3490ae7e3215f02c9c5471944e0a2`。导出接口采信了新报告，原有两期收益工件、冻结分母及校正结果一致。两套实测的 `round-final-20261117/summary.json` 和主样本的 `updated-validation.json` 记录了对应的报告身份与分项结果。

在仓库根目录逐条运行以下命令可重建本地材料。前三个采集命令的 `--fetch` 仅获取缺少的响应，本地已有收据时校验并复用原始字节。复测目录应是新目录。使用新下载的上游版本时，从脚本输出复制本次的 manifest ID，并使用相应的日历版本。

```text
python -m scripts.freeze_us_benchmark --root data/us_public_benchmark --start 2023-01-03 --end 2025-12-31 --fetch
python -m scripts.freeze_us_public_sample --root data/us_public_eodhd_2025 --end 2025-12-31 --benchmark NASDAQXNDX --benchmark-source-root data/us_public_benchmark --fetch
python -m scripts.freeze_kibot_us_sample --root data/us_public_kibot_2025 --action-root data/us_public_eodhd_2025 --benchmark NASDAQXNDX --benchmark-source-root data/us_public_benchmark --fetch
python -m scripts.verify_multisecurity_round --manifest-id ds_98ac585dc7b96ef10e971080870da4a1 --import-root data/us_public_kibot_2025/imports --evidence-root data/us_public_kibot_2025/evidence --universe kibot_guest_us_equities --universe-version kibot-guest-fixed/v1 --calendar-version exchange-calendars/4.13.2:XNYS --benchmark NASDAQXNDX --selection-sessions 75 --holdout-sessions 65 --work-dir data/us_public_kibot_2025/round-replay
```
