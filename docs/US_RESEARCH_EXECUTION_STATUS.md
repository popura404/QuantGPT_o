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
