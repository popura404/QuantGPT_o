# QuantGPT MCP 配置指南

QuantGPT 提供标准 MCP (Model Context Protocol) 接口，支持因子研究工具、StrategySpec v0 策略工具，以及当前仓库已实现的 Post-MVP StrategySpec v1 扩展。可通过 Claude Code、Claude Desktop 等 MCP 客户端直接调用。

## 快速开始（推荐）

### Claude Code

在项目根目录添加 `.mcp.json`（stdio 模式，已验证可用）：

```json
{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
      "args": ["-m", "quantgpt"],
      "cwd": "/absolute/path/to/quantgpt"
    },
    "deepseek": {
      "type": "stdio",
      "command": "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
      "args": ["scripts/mcp_deepseek.py"],
      "cwd": "/absolute/path/to/quantgpt"
    }
  }
}
```

**关键要点：**

1. **必须用 stdio 模式** — Claude Code 对 `streamable-http` / `sse` 类型支持不稳定，stdio 最可靠
2. **command 必须用 Python 绝对路径** — 如 `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`，不要用 `python3`（Claude Code 的子进程环境可能找不到）
3. **cwd 必须用绝对路径** — 指向项目根目录，确保 `python3 -m quantgpt` 能找到包
4. **deepseek MCP 需要 `.env` 中配置 `DEEPSEEK_API_KEY`** — 脚本会自动从 `.env` 读取

配置完成后**重启 Claude Code**（退出后重新进入项目目录），验证连接：

```bash
# 在 Claude Code 中输入
/mcp
# 应显示 quantgpt: connected, deepseek: connected
```

### Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）：

```json
{
  "mcpServers": {
    "quantgpt": {
      "command": "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
      "args": ["-m", "quantgpt"],
      "cwd": "/absolute/path/to/quantgpt"
    }
  }
}
```

### 从 GitHub 安装

```bash
# 克隆项目
git clone https://github.com/Miasyster/QuantGPT.git
cd QuantGPT

# 安装依赖
pip install -e .

# 配置（在 .env 中设置 DeepSeek API Key，米筐账号可选）
cp .env.example .env
# 编辑 .env

# .mcp.json 已包含在仓库中，重启 Claude Code 即自动连接
```

### 常见问题

**Q: MCP 连不上？**

1. 确认 `command` 是绝对路径，运行 `which python3` 获取
2. 确认 `cwd` 指向项目根目录（包含 `quantgpt/` 子目录的那层）
3. 确认 `pip install -e .` 已执行（quantgpt 包已安装）
4. 修改 `.mcp.json` 后必须重启 Claude Code

**Q: HTTP 模式（streamable-http）能用吗？**

MCP 同时挂载在 HTTP 服务上（`/mcp/` 和 `/mcp-sse/`），但需要先启动 HTTP 服务（`bash restart.sh`），且 `mcp_server.py` 的 `allowed_hosts` 必须包含带端口的 host（如 `localhost:8003`）。stdio 模式无此限制，推荐优先使用。

---

## 工具列表

### 因子研究工具

| 工具 | 说明 |
|------|------|
| `list_operators` | 返回全部因子表达式算子及用法 |
| `list_universes` | 返回可用股票池和基准指数 |
| `validate_expression` | 验证因子表达式语法 |
| `run_backtest` | 执行因子回测，生成 HTML 报告 |
| `score_factor` | 因子综合评分 (0-100, A/B/C/D) |
| `compute_factor_values` | 输出每日全市场截面因子值 |
| `diagnose_factor` | 诊断因子问题，推荐改进策略 |
| `run_anti_overfit` | 反过拟合检测 (4 项测试) |
| `run_rolling_validation` | 滚动验证 (Walk-Forward) |
| `wq_brain_submit` | 提交因子到 WorldQuant BRAIN |
| `ask_deepseek` | 调用 DeepSeek LLM 进行研究评审（独立 MCP） |

### StrategySpec v0 策略工具

这些工具保持非实盘边界。`export_strategy_candidate` 只输出候选调仓信号，
不是订单协议，也不包含 broker/account/order/api_key/execution 字段。

| 工具 | 说明 |
|------|------|
| `list_markets` | 返回策略框架支持的市场；MVP 仅 `a_share` |
| `list_data_fields` | 返回指定市场可用于策略因子表达式的数据字段 |
| `validate_strategy_spec` | 校验 `StrategySpecV0`，失败时返回 `error_code` 和 `hint` |
| `run_strategy_backtest` | 运行单因子 top quantile 等权策略回测 |
| `score_strategy` | 根据策略回测结果计算策略级评分 |
| `generate_strategy_report` | 根据策略回测结果生成 HTML 报告和 summary JSON |
| `export_strategy_candidate` | 导出候选调仓信号，不包含执行字段 |
| `diagnose_strategy` | 输出策略诊断 taxonomy 和 spec 调整建议 |
| `run_strategy_anti_overfit` | 基于策略回测结果执行策略级反过拟合摘要 |
| `run_strategy_rolling_validation` | 基于策略收益执行 rolling validation 摘要 |
| `list_strategy_templates` | 返回可用策略模板和治理边界 |
| `get_strategy_template` | 返回指定模板的 spec 和治理元数据 |
| `instantiate_strategy_template` | 从模板生成可校验 StrategySpec |
| `optimize_strategy_candidate` | 在风控约束下优化候选权重 |

### 通用参数

以下参数在 `run_backtest`、`score_factor`、`run_anti_overfit`、`run_rolling_validation` 中通用：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `expression` | str | 必填 | 因子表达式 |
| `universe` | str | `hs300` | 股票池：`hs300` / `csi500` / `csi1000` / `csi2000` / `small_scale` |
| `start_date` | str | `2023-01-01` | 回测起始日期 |
| `end_date` | str | `2025-12-31` | 回测结束日期 |
| `n_groups` | int | `5` | 分组数量 |
| `holding_period` | int | `5` | 持仓周期（交易日） |
| `benchmark` | str | `hs300` | 基准指数：`hs300` / `zz500` / `sz50` |
| `neutralize_industry` | bool | `true` | 行业中性化 |
| `neutralize_cap` | bool | `true` | 市值中性化 |

---

### `compute_factor_values`

计算某个因子表达式在指定股票池和日期区间内的每日截面得分。该工具只输出原始因子值，不执行分组回测，也不生成报告。

参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `expression` | str | 必填 | 因子表达式 |
| `universe` | str | `csi500` | 股票池：`small_scale` / `hs300` / `csi500` / `csi1000` / `csi2000` |
| `start_date` | str | `end_date` 前 365 天 | 起始日期 YYYY-MM-DD |
| `end_date` | str | 今天 | 结束日期 YYYY-MM-DD |

示例：

```json
{
  "expression": "rank(ts_mean(close/open, 10))",
  "universe": "csi500",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31"
}
```

返回：

```json
{
  "expression": "rank(ts_mean(close/open, 10))",
  "universe": "csi500",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "trading_days": 1,
  "data": [
    {
      "date": "2025-01-02",
      "values": {
        "sh.600000": 0.812345
      },
      "count": 1
    }
  ]
}
```

日期跨度最大 750 天。服务会在起始日前额外取 260 天行情用于滚动表达式预热。

---

## 使用示例

### Agent 工作流

```
1. list_operators         → 了解可用算子
2. 构造因子表达式
3. validate_expression    → 确认语法正确
4. score_factor           → 快速评分
5. run_backtest           → 完整回测 + HTML 报告
6. diagnose_factor        → 诊断改进方向
7. run_anti_overfit       → 检查过拟合风险
8. run_rolling_validation → 样本外验证
```

### StrategySpec v0 工作流

```
1. list_markets                 → 确认可用市场
2. list_data_fields             → 确认可用字段
3. validate_strategy_spec       → 校验结构化策略
4. run_strategy_backtest        → 生成策略收益、目标权重和风控日志
5. score_strategy               → 计算策略级评分
6. generate_strategy_report     → 生成 HTML 报告和 summary JSON
7. export_strategy_candidate    → 导出候选信号
8. diagnose_strategy            → 获取诊断与修改建议
```

最小 `validate_strategy_spec` 输入：

```json
{
  "schema_version": "strategy_spec/v0",
  "name": "simple_momentum_top_quantile",
  "asset_class": "equity",
  "market": "a_share",
  "frequency": "daily",
  "universe": "hs300",
  "factors": [
    {
      "id": "momentum_20d",
      "expression": "rank(close / ts_mean(close, 20))",
      "direction": "higher_is_better",
      "weight": 1.0
    }
  ],
  "signal_rules": { "type": "rank_threshold", "long_quantile": 0.2 },
  "portfolio_rule": { "weighting": "equal_weight", "rebalance_period": 5 },
  "risk_rules": { "allow_short": false, "max_asset_weight": 0.05, "max_turnover": 0.8 },
  "cost_model": { "type": "fixed_bps", "bps": 30 },
  "validation": {
    "min_history_days": 252,
    "run_strategy_anti_overfit": false,
    "run_strategy_rolling_validation": false
  },
  "outputs": { "report": true, "signal_export": false }
}
```

错误示例：

```json
{
  "error_code": "MARKET_UNSUPPORTED",
  "hint": "MVP only supports market=a_share through AShareAdapter."
}
```

Post-MVP 已提供 StrategySpec v1、多因子、top N、独立 SignalExport、策略级 rolling/anti-overfit、模板和候选权重优化。所有策略工具仍只输出研究候选、目标权重、报告和审计信息，不提供券商账户、真实订单或自动下单能力。

### 常用因子表达式

```python
# 20日动量
rank(close / ts_mean(close, 20))

# 成交量异动
rank(volume / ts_mean(volume, 10))

# 波动率因子
ts_std(close / ts_shift(close, 1) - 1, 20)

# 反转因子
rank(-1 * ts_delta(close, 5) / ts_shift(close, 5))

# 量价背离
rank(ts_corr(close, volume, 10))

# ROE 动量（基本面）
rank(ts_delta(roe, 60))
```

---

## 股票池

| 名称 | 说明 | 成分股数量 |
|------|------|-----------|
| `small_scale` | 蓝筹测试池 | 5 |
| `hs300` | 沪深300 | ~300 |
| `csi500` | 中证500 | ~500 |
| `csi1000` | 中证1000 | ~1000 |
| `csi2000` | 中证2000 | ~2000 |

---

## 数据源

- **akshare / baostock**：免费数据源，回测流程默认使用，自动缓存到 `data/stocks/*.parquet`
- **rqdatac（米筐）**：仅手动触发（admin 端点 / prewarm 脚本），需在 `.env` 中配置 `RQDATAC_USERNAME` 和 `RQDATAC_PASSWORD`
- 首次使用会自动下载并缓存数据，后续直接读取

---

## HTTP 服务模式（可选）

启动 HTTP 服务后，MCP 自动挂载到两个端点：

```bash
bash restart.sh   # 启动 HTTP 服务（端口 8003）
```

| 端点 | 协议 | 说明 |
|------|------|------|
| `/mcp/` | streamable-http | 推荐（需 `Accept: application/json, text/event-stream`） |
| `/mcp-sse/` | SSE | 兼容旧客户端 |

`mcp_server.py` 中的 `allowed_hosts` 需包含带端口的 host：

```python
allowed_hosts=["localhost", "localhost:8003", "127.0.0.1", "127.0.0.1:8003"]
```

> stdio 模式（`.mcp.json` 配置）不依赖 HTTP 服务运行，是 Claude Code 的推荐方式。
