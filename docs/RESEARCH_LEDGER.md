# 研究持仓账本与计算修复

对应 P04/P05，2026-10-05。实现 `quantgpt/research/ledger.py`，合成数值与实际入口回归分别位于 `tests/test_research_ledger.py` 和 `tests/test_research_backtest_regressions.py`。
当前状态为实现及契约测试阶段，真实外部集成未验收。

本地策略入口和因子分组入口已调用同一个数量/现金/应收账本。它按照 [研究契约](RESEARCH_CONTRACTS.md) 的公司行动→开盘成交→收盘估值顺序执行，保留固定数量，费用按实际买卖金额分别计提。
首次建仓收费，目标仓位通过求解扣费后的NAV保留费用资金；不能隐式透支。初始NAV进入回撤高水位，首日亏损不会消失。

`simulate_target_weights(frame, targets, config, evaluation_start=..., evaluation_end=..., max_turnover=...)` 接收上一收盘的目标权重，并在共同会话的下一次开盘成交。
`frame.attrs['calendar_sessions']` 保存全局交易会话序列；调仓日先在该序列按锚点选取，再切评分窗。缺少更早锚点的日历上下文返回 `CALENDAR_CONTEXT_REQUIRED`，不使用工作日猜测交易所会话。
`frame.attrs['corporate_actions']` 接收 `event_id/security_id/session/type` 事件，split 另含 ratio，cash_dividend 另含 per_share/pay_session。
拆股在开盘前调整数量，现金分红按旧持仓确认应收并在支付会话转现金；新开盘持仓不获取此前权益。
换股、分拆、现金并购等尚无账本处理器的事件，持有相关资产时明确阻断。

`fresh_cash` 评分窗从现金开始，warmup 仅生成特征。`carry_forward` 需要之前状态的内容引用、现金、数量和应收，来源必须恰为评分窗前一共同会话；更早状态必须先回放中间事件，不能跳过。
策略运行可传 `simulation_config`，其费用/调仓周期/显式锚点需与旧spec约束一致。
多分组因子评价尚不接受一个通用carry-forward引用，因为每组需要自己的起始账本；该请求会明确返回 capability blocker。

信号行与估值行分开：缺因子不产生新目标，仍持有资产继续完整估值；缺估值价阻断，不能记零或删持仓。缺开盘价/停牌默认阻断，显式 skip_and_hold 可整次跳过调仓并维持数量。
输入已标记 qfq/hfq/split_adjusted/total_return 的数据不得伪装raw进入账本，返回 `INCOMPATIBLE_PRICE_BASIS`。
未声明价格口径的手工frame可用于合成研究，结果的 price_basis_status 为 unverified_supplied_frame，不能据此认为真实行情已验收。
现有A股provider曾固定保存qfq；使用这类缓存时需取得raw数据后重算，不能为维持旧路径隐瞒其口径。

factor的long-short指标只作研究差值展示，gross exposure=1，以一半多头减一半空头毛收益后扣两边费用；不是具备借券、融资、保证金机制的可执行short策略。
该差值来自两个long-only账本的实际交易路径，报告明确 `informational_unfinanced_spread`；不宣称具备真实空头账本。
旧自动方向选择仍标 `auto_full_deprecated`，只适合描述性探索；OOS继续在训练窗冻结方向。

全部组件因子默认都需有效；无穷视为缺失，nested lookback 通过AST推断并mask不足观测。多因子IC基于方向调整后的复合信号，交换因子顺序不改变结果。
IC当前采用共同会话上的收盘到收盘horizon，属于预测指标，不冒充开盘可成交收益。递归指标初始化与跨缺口完整市场日历的上游补齐仍需能力证据。

策略adapter读取行情时也读取benchmark；注入行情fixture时，可另外传 `benchmark_returns`。只有完整评分会话覆盖、total_return、同币种和分红再投资政策均明确时才输出基准总收益和超额总收益。
未取得口径时显示 unavailable/unknown_return_basis 等状态；非零benchmark_cost_bps尚缺相应成交模型，因此明确阻断基准比较。
结果保存精简账本配置/交易次数/期末净值以及独立benchmark序列。完整账本的持仓/交易表由后续artifact service归档，暂不塞入默认工具摘要。

验证命令使用已锁定 Python 3.12 环境：

```powershell
.\.venv312\Scripts\python.exe -m pytest tests/test_research_contracts.py tests/test_research_ledger.py tests/test_research_backtest_regressions.py tests/test_backtest.py tests/test_strategy_backtest.py tests/test_strategy_oos.py tests/test_oos_validation.py -q --basetemp=test-results/ledger -o cache_dir=test-results/pytest-cache-ledger
.\.venv312\Scripts\python.exe -m pyright --pythonpath .\.venv312\Scripts\python.exe quantgpt/research/contracts.py quantgpt/research/ledger.py
```

测试消费同一份 `tests/fixtures/research/simulation_golden.json`，另覆盖恒价换仓成本、A/B价格100→200→100持有回归、停牌与缺价、全缺失信号、因子换序、完整会话锚点、warmup切窗和基准口径。
这份合成验收不代替真实公司行动/provider覆盖、数据许可、独立final门禁或发布性能验证；历史报告保留为legacy结果，修复后必须重新计算。
