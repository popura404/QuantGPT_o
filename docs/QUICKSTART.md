# QuantGPT Quick Start

## Prerequisites

- CPython 3.12
- Node.js 20+ and npm if you want the browser workbench

## 1. Clone & Setup

```bash
git clone https://github.com/popura404/QuantGPT_o.git
cd QuantGPT_o
```

On Linux, install the locked Python dependencies from the repository root.

```bash
make setup
```

On Windows, use PowerShell.

```powershell
.\scripts\setup.ps1
```

Both commands install the package in `.venv312` and copy `.env.example` to `.env` on first setup.
The default is `AUTH_DISABLED=false`. Before starting the HTTP service, set a random
`JWT_SECRET_KEY` of at least 32 characters and replace the sample `QUANTGPT_ADMIN_PASSWORD` in your local `.env`.

Use [DEPLOYMENT.md](DEPLOYMENT.md) for Docker, server, reverse-proxy, backup, and rollback procedures.

Expression-only evaluation works without an LLM API key.

For MCP configs, replace `/path/to/QuantGPT_o` with the absolute path of the checkout you are running.

## 2. Start the Server

Start the HTTP API with `make dev` on Linux or `.\scripts\dev.ps1` in PowerShell.
Both listen on `127.0.0.1:8003`. Open `http://127.0.0.1:8003/api/v1/health` to check the service.

For the browser workbench, build the frontend before starting the server. Use `make frontend` on Linux.
In PowerShell, run these commands from the repository root.

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
```

After starting the server, open `http://127.0.0.1:8003`.

## 3. Agent Mode (Recommended)

Add MCP configuration to Claude Code or Claude Desktop:

```json
{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "/path/to/QuantGPT_o/.venv312/bin/python",
      "args": ["-m", "quantgpt"],
      "cwd": "/path/to/QuantGPT_o"
    }
  }
}
```

On Windows, use the absolute path to `.venv312/Scripts/python.exe` instead. The client launches the
stdio process directly. To verify the connection, ask it to list the tools or call `list_operators`.

When you want to begin research, give the agent a direction.
```
在沪深300上挖掘高 fitness 的因子，目标 WQ BRAIN 可提交
```

单股问题（例如 `600487`）先让 Agent 调用 `get_stock_history`，需要检查股票池月份或 parquet 覆盖时再调用
`check_market_cache`。不要把单股问题直接升级为 `compute_factor_values(csi500)` 或 `score_factor(csi500)`。

长任务优先走异步 MCP：对 `run_backtest`、`score_factor`、`compute_factor_values`、`run_anti_overfit`、
`run_rolling_validation` 传入 `submit_only=true`，拿到 `task_id` 后用 `get_mcp_task_status` 查询阶段、进度、
错误和结果；如果研究方向需要中止，用 `cancel_mcp_task` 请求协作式取消。

这里的“可提交”应理解为生成可模拟/可候选的 WQ BRAIN 表达式；正式 submit 仍需要本地
preflight 通过，或显式提供 `submission_override_reason` 记录豁免理由。

## 4. Expression Mode (No LLM Required)

Via API:
```bash
curl -X POST http://localhost:8003/api/v1/auto_backtest \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"prompt": "rank(close / ts_mean(close, 20))", "universe": "hs300"}'
```

With the default authentication settings, replace `<token>` with a signed-in user's access token
or API key. You can also sign in to the browser workbench and enter an expression there.

## 5. StrategySpec Mode

Use this when the Agent has produced a structured `StrategySpecV0` instead of a
single factor expression:

```bash
curl -X POST http://localhost:8003/api/v1/strategy/validate \
  -H "Content-Type: application/json" \
  -d '{
    "spec": {
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
  }'
```

Then submit an async strategy backtest:

```bash
curl -X POST http://localhost:8003/api/v1/strategy/backtest \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "spec": {
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
    },
    "start_date": "2024-01-02",
    "end_date": "2024-03-29",
    "benchmark": "hs300"
  }'
```

`StrategySpecV0` strategy mode is intentionally narrow: A-share only, one factor,
top quantile, equal weight, an HTML report, and server-side summary artifact.
`StrategySpecV1` adds implemented Post-MVP capabilities such as templates,
multi-factor/top-N specs, strategy persistence, OOS/data-quality validation,
strategy-level diagnostics, rolling/anti-overfit summaries, candidate export,
optimization, and a browser workbench. Open the `策略工作台` tab after starting
the server to instantiate a template, edit JSON, validate it, submit a strategy
backtest, track task status, view score/metrics/holdings, open the generated
report, and run candidate export for signals. The same workflow is available
through:

```bash
curl http://localhost:8003/api/v1/strategy/templates
curl -X POST http://localhost:8003/api/v1/strategy/templates/momentum_top_n_v1/instantiate \
  -H "Content-Type: application/json" \
  -d '{"overrides": {"signal_rules.top_n": 10}}'
```

The Post-MVP signal export and optimizer still produce research candidates only;
they do not create broker/account/order instructions.

## 6. Try More Expressions

```
# Price relative to the 20-session average
rank(close / ts_mean(close, 20))

# Volume anomaly
rank(volume / ts_mean(volume, 10))

# Value factor (needs fundamental data)
rank(-1 * pe)
```

## 7. Enable DeepSeek (Optional, for factor generation & cross-review)

1. Get a DeepSeek API key from [platform.deepseek.com](https://platform.deepseek.com)
2. Edit `.env`:
   ```
   DEEPSEEK_API_KEY=sk-your-key-here
   ```
3. Restart the process that will use the key. For HTTP, use `make dev` on Linux or `.\scripts\dev.ps1` on Windows. For stdio, reconnect the MCP client.

## What's Next

- Read [DEPLOYMENT.md](DEPLOYMENT.md) for Docker/server deployment and operations
- Read [STRATEGY_SPEC.md](STRATEGY_SPEC.md) for StrategySpec v0/v1 and non-trading boundaries
- Read [ARCHITECTURE.md](ARCHITECTURE.md) for system design
- Check [MCP_GUIDE.md](MCP_GUIDE.md) for MCP tool details
- Read [FACTOR_MINING.md](FACTOR_MINING.md) for the autonomous research loop
- Use the [shared research workflow](agent_safe_workflow.md) to coordinate multiple research directions.
