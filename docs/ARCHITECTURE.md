# Architecture

QuantGPT 是 Agent-Driven 的因子研究引擎。核心架构分为六层：Agent 接口、表达式引擎、回测引擎、验证体系、数据管道、进化引擎。

## System Overview

```
LLM Agent (Claude Code / Claude Desktop)
    │
    ├── MCP Tools (39 个)            ← Agent 的工具箱
    │   ├── 因子研究                 ← 表达式校验、回测、评分、诊断、OOS/rolling、factor_values
    │   ├── StrategySpec 工作流       ← 模板、校验、回测、评分、报告、候选导出、优化
    │   └── WQ BRAIN 工作流          ← 远程模拟、批量扫描、状态检查、preflight-gated 正式提交
    │
    ├── REST API                      ← 程序化访问
    │   ├── /api/v1/auto_backtest
    │   ├── /api/v1/factor_values
    │   ├── /api/v1/strategy/...
    │   ├── /api/v1/wq-brain/batch-submit
    │   ├── /api/v1/wq-brain/{task_id}/submit-alpha
    │   └── ...
    │
    └── Web UI (browser workbench)    ← 因子、策略、WQ、任务和报告工作台
```

## 1. Expression Parser (`expression_parser.py`)

将因子表达式字符串解析为可作用于 DataFrame 的函数。

**关键设计**：
- **截面 vs 时序分离**：`rank()`, `zscore()` 按 `trade_date` 分组计算（截面算子）；`ts_mean()`, `ts_corr()` 按 `stock_code` 分组计算（时序算子）
- **递归下降解析**：支持嵌套、运算符优先级、比较/逻辑操作
- **安全约束**：`MAX_DEPTH=100` 防止栈溢出，窗口上限防止 OOM

**算子分类**：

| 类型 | 算子 | 分组方式 |
|------|------|----------|
| 截面 | rank, zscore, scale, sign | 按 trade_date |
| 时序 | ts_mean, ts_std, ts_corr, decay_linear, ... | 按 stock_code |
| 分组 | group_rank, group_zscore, group_neutralize | 按 group × trade_date |
| 技术指标 | ema, sma, rsi, macd, atr, boll_* | 按 stock_code |
| 无状态 | abs, log, sqrt, tanh, sigmoid | 无分组 |
| WQ-only 远程 | vector_neut, trade_when, pasteurize, bucket, vec_*, indneutralize | 仅语法校验 |

**双模式**：`mode="wq"` 支持 WQ BRAIN 全字段（价量 + 基本面 + MDF + 新闻 + 期权 + 关系数据 80+ 字段）和 WQ-only 远程算子（30+），本地做语法校验但不执行计算；`mode="local"` 开放本地可计算的全部算子。

## 2. Backtest Engine (`backtest.py`)

Rank-based 分组回测引擎。

**流程**：
1. 应用因子表达式到全市场 DataFrame
2. 按因子值排序，分为 N 个 quantile 组
3. 在调仓日重新分组，组内等权
4. 计算每组日收益率序列
5. Top 组作为策略收益，Bottom 组作为对照

**关键防偏措施**：
- **Lookahead bias 防护**：`searchsorted(..., side="left")` 延迟组分配到 T+1
- **交易成本**：基于换手率的单边成本模型，在调仓日次日扣除
- **IC 计算**：因子 T 与 forward N-day return 的 Pearson/Spearman 相关
- **API Context Guard**：`_require_api_context()` 强制所有回测走 API 路径

## 3. Validation Suite

三层验证体系，每层独立评估因子质量。

### 3.1 Anti-Overfit (`anti_overfit.py`)

四项统计检验：
- **IC 稳定性**：滚动 IC 的变异系数
- **子样本压力测试**：牛市/熊市/震荡市分段表现
- **安慰剂检验**：随机打乱因子值，验证原始因子是否显著优于随机
- **半衰期估计**：IC 自相关衰减速度

### 3.2 Walk-Forward (`rolling_validator.py`)

滚动窗口验证：
- 数据切分为 train/valid/test 窗口
- 每个窗口独立计算 IC/IR
- 评估样本外衰减程度

### 3.3 OOS Validation and Data Quality (`validation/`, `data_quality.py`)

因子和 `StrategySpecV1` 都可以走更严格的 train/valid/test 样本外闭环：
- 基础行情先经过 data-quality gate，记录缺列、OHLC、极端收益和缺失率问题
- A 股第一档现实约束在元数据可用时同步处理：ST/*ST、停牌、新股窗口、一字涨跌停不可成交、
  `close/pre_close` 与 `pct_change` 的复权一致性
- 训练期确定方向，验证期和测试期使用固定方向或 StrategySpec 声明方向
- Agent/结论型因子评分默认停在 `validation_stage="selection"`：train 定方向/参数，valid 选候选，
  test withheld；`validation_stage="final"` 才运行并暴露 test 终验
- OOS payload 暴露 `data_quality`、`oos_result`、`oos_summary` 和 `oos_score`
- OOS scoring 优先使用验证/测试表现、样本外衰减、换手和数据质量惩罚
- `validation/promotion.py` 是 candidate / submit / export 的统一晋级门禁：
  `factor_validation/v1` 必须同时记录并通过 data-quality、train/valid/test、
  rolling window、placebo 和 time-shift。普通 `auto_full` 回测和未跑完整 suite 的
  OOS 回测都标记为 `research_only`，不得直接进入提交或导出。

### 3.4 WQ BRAIN Simulation (`wq_simulate.py`)

对齐 WorldQuant BRAIN 的回测逻辑：
- Dollar-neutral 多空组合
- Fitness = Sharpe × √(|Returns| / max(Turnover, 0.125))
- IS test compatibility scoring

正式提交到 WQ BRAIN 前会经过 `wq_submission_guard.py` 本地 preflight：
远程模拟不强制本地执行，但 `auto_submit`、手动 submit 和 by-id submit 都必须先通过
完整 promotion preflight gate。默认本地 A 股 `a_share` / `hs300` 检查不能授权 WQ `USA` / `TOP3000`
目标，范围不一致会返回 `LOCAL_PREFLIGHT_SCOPE_MISMATCH`；WQ-only、缺少表达式溯源、
本地不可执行或接受远程 WQ 验证代替本地代理验证时，需要显式 `submission_override_reason`。

## 4. Data Pipeline (`market_data.py`)

多数据源 + Parquet 缓存。

```
Request
  │
  ├──▶ Parquet Cache (local, zero-latency)
  │       │ miss
  ├──▶ baostock (free, T+1 delay)
  │       │ miss
  └──▶ akshare (free, same-day data)
```

**缓存策略**：
- 按股票单独缓存为 Parquet 文件：`data/stocks/{code}.parquet`
- 请求时先检查缓存覆盖范围，仅增量获取缺失数据

**股票池**：
- `small_scale`：5 只蓝筹（静态，快速测试用）
- `hs300`：沪深 300（动态获取成分股）
- `csi500`/`csi1000`/`csi2000`：中证系列

## 5. Evolution Engine

### Evolutionary Search (`iteration.py`, `mutation_engine.py`, `crossover_engine.py`)

三阶段因子迭代：
1. **Trajectory Analyzer**：评估因子质量轨迹（探索多样性、收敛速率、稳定性）
2. **Meta-Evolution Selector**：自适应策略选择（EXPLOIT / EXPLORE / RECOMBINE / SIMPLIFY）
3. **Execution**：8 种定向突变 + 高分因子交叉重组

### MCP Server (`mcp_server.py`)

39 个 MCP 工具，供 Claude Code / AI Agent 直接调用：
- 因子研究：`list_operators` / `list_universes` / `validate_expression` /
  `run_backtest` / `score_factor` / `compute_factor_values` / `diagnose_factor` /
  `run_anti_overfit` / `run_rolling_validation`
- 实验治理：`list_experiments` / `get_experiment` / `export_experiment_report` /
  `compare_experiments` / `show_factor_lineage` / `summarize_trial_counts` /
  `find_similar_factors` / `run_multiple_testing_check` / `promote_experiment` /
  `reject_experiment`
- StrategySpec：`list_markets` / `list_data_fields` / `list_strategy_templates` /
  `get_strategy_template` / `instantiate_strategy_template` / `validate_strategy_spec` /
  `run_strategy_backtest` / `score_strategy` / `generate_strategy_report` /
  `export_strategy_candidate` / `diagnose_strategy` / `run_strategy_anti_overfit` /
  `run_strategy_rolling_validation` / `optimize_strategy_candidate`
- WQ BRAIN：`wq_brain_submit` / `wq_brain_batch_submit` /
  `wq_brain_submit_by_ids` / `wq_brain_list_alphas` / `wq_brain_check_alphas` /
  `wq_brain_finalize_submissions`

### WQ BRAIN Integration (`wq_brain_client.py`)

- 认证 → 远程模拟 → IS/SC 检查 → preflight-gated 正式提交，全流程 API
- Alpha Tracker：已提交 alpha 记录 + 自相关预筛
- 批量参数扫描：region × delay × universe × neutralization 网格
- 正式 submit 路径统一经过 `wq_submission_guard.py`；本地 preflight 不可用或范围不一致时必须记录
  `submission_override_reason`

## 6. Database

SQLAlchemy 2.0 async ORM，支持 SQLite（默认）和 PostgreSQL。

**核心表**：
- `users` — 用户账户
- `tasks` — 回测任务（状态机：pending → running → completed/failed）
- `reports` — HTML 报告文件记录
- `saved_factors` — 用户保存的因子
- `submitted_alphas` — WQ BRAIN 已提交 alpha 记录
- `factor_search_attempts` — 因子搜索尝试的不可变审计记录
- `strategies` / `strategy_runs` — StrategySpec 和策略回测运行记录
- `api_keys` — API key 哈希和权限范围
- `daily_summaries` — 每日市场摘要

## 7. Frontend

React 18 + TypeScript + Vite + Tailwind CSS 4。定位为浏览器工作台，Agent 可以通过 MCP 工作，Web UI
用于因子研究、策略工作流、WQ BRAIN 操作、任务中心和报告查看。

**组件层次**：
```
App
├── ResearchDashboard     # 研究总览（统计/筛选/详情）
├── BacktestForm          # 因子输入 + 参数配置
├── ProgressTracker       # SSE 实时进度
├── ResultsDashboard      # 结果可视化
├── IterationPanel        # AI 迭代优化
├── FactorLibrary         # 因子库管理
├── CompositeBuilder      # 多因子组合
├── FactorComparison      # 因子对比
├── StrategyWorkbench     # StrategySpec 校验/回测/评分/导出
├── WQBrainWorkspace      # WQ BRAIN 模拟、preflight 和提交操作
└── TaskCenter            # 会话任务中心
```
