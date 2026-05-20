# QuantGPT API 完整文档

> 版本: v1 | 基础路径: `/api/v1` | REST 认证: Bearer Token (JWT/API Key)

---

## 目录

1. [启动与配置](#启动与配置)
2. [认证](#认证)
3. [会话管理](#会话管理)
4. [回测](#回测)
5. [因子截面值](#因子截面值)
6. [策略框架](#策略框架)
7. [实时推送 (SSE)](#实时推送-sse)
8. [迭代优化](#迭代优化)
9. [报告](#报告)
10. [反馈](#反馈)
11. [管理后台](#管理后台)
12. [MCP Tools](#mcp-tools)
13. [健康检查](#健康检查)
14. [错误码](#错误码)
15. [股票池与基准](#股票池与基准)
16. [因子表达式语法](#因子表达式语法)

---

## 启动与配置

### 启动命令

```bash
# HTTP 服务 (含前端)
bash restart.sh   # 默认端口 8003

# MCP 服务 (stdio)
python -m quantgpt

# 数据预热
python -m quantgpt --prefetch hs300 csi500
```

### 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DATABASE_URL` | 否 | SQLite `./quantgpt.db` | 数据库连接串；留空使用本地 SQLite |
| `DEEPSEEK_API_KEY` | 否 | — | DeepSeek API Key；留空进入表达式模式 |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com/v1` | LLM API 地址 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-chat` | 模型名称 |
| `AUTH_DISABLED` | 否 | `false` | 仅本地开发可设为 `true`；生产必须保持 `false` |
| `JWT_SECRET_KEY` | 是 | — | JWT 签名密钥；生产使用 `openssl rand -hex 32` 生成 |
| `QUANTGPT_MCP_HTTP_TOKEN` | 条件必填 | — | `AUTH_DISABLED=false` 且暴露 `/mcp` 或 `/mcp-sse` 时必填 |
| `QUANTGPT_CORS_ORIGINS` | 否 | `*` | CORS 允许源,逗号分隔 |
| `QUANTGPT_ADMIN_PASSWORD` | 是 | — | 管理后台密码；生产必须使用强随机密码 |
| `QUANTGPT_MAX_ACTIVE_TASKS` | 否 | `100` | 最大并发任务数 |
| `QUANTGPT_TASK_TTL` | 否 | `3600` | 内存任务 TTL (秒) |
| `QUANTGPT_RATE_LIMIT` | 否 | `10` | 每分钟请求限制 |
| `QUANTGPT_MAX_PROMPT_LEN` | 否 | `500` | Prompt 最大长度 |
| `QUANTGPT_FEEDBACK_WEBHOOK` | 否 | — | 飞书 Webhook URL |
| `QUANTGPT_FEEDBACK_WEBHOOK_SECRET` | 否 | — | 飞书签名密钥 |

---

## 认证

REST API 默认需要 Bearer Token（JWT access token 或 `qgpt_` API Key）。健康检查、认证入口和只读公开页面除外。`AUTH_DISABLED=true` 只允许本地开发使用；生产必须保持 `AUTH_DISABLED=false`，并配置强 `JWT_SECRET_KEY` 与强 `QUANTGPT_ADMIN_PASSWORD`。

```
Authorization: Bearer <access_token>
```

HTTP MCP 端点不复用用户 JWT。认证开启时，`/mcp` 和 `/mcp-sse` 需要独立的 `Authorization: Bearer <QUANTGPT_MCP_HTTP_TOKEN>`；未配置该变量时 HTTP MCP 返回 503。stdio MCP 不经过 HTTP 暴露面，按本机进程权限控制。

### POST /api/v1/auth/send-code

发送邮箱验证码。

**请求体:**

```json
{
  "email": "user@example.com"
}
```

**响应 200:**

```json
{
  "message": "验证码已发送",
  "expires_in": 300
}
```

**错误:** 429 (发送过于频繁), 400 (邮箱格式错误)

---

### POST /api/v1/auth/verify-code

验证码登录/注册。首次登录自动注册。

**请求体:**

```json
{
  "email": "user@example.com",
  "code": "123456"
}
```

**响应 200:**

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "created_at": "2026-01-01T00:00:00"
  }
}
```

**错误:** 400 (验证码错误/过期), 429

---

### POST /api/v1/auth/refresh

刷新 Access Token。

**请求体:**

```json
{
  "refresh_token": "eyJ..."
}
```

**响应 200:**

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

---

### GET /api/v1/auth/me

获取当前用户信息。

**响应 200:**

```json
{
  "id": "uuid",
  "email": "user@example.com",
  "created_at": "2026-01-01T00:00:00"
}
```

---

## 会话管理

会话用于组织一系列相关的回测任务。

### POST /api/v1/sessions

创建新会话。

**请求体:**

```json
{
  "name": "动量因子研究"
}
```

**响应 201:**

```json
{
  "id": "uuid",
  "name": "动量因子研究",
  "created_at": "2026-01-01T00:00:00"
}
```

---

### GET /api/v1/sessions

列出当前用户的所有会话 (按更新时间倒序)。

**响应 200:**

```json
{
  "sessions": [
    {
      "id": "uuid",
      "name": "动量因子研究",
      "created_at": "2026-01-01T00:00:00",
      "updated_at": "2026-01-02T00:00:00"
    }
  ]
}
```

---

### PATCH /api/v1/sessions/{session_id}

重命名会话。

**请求体:**

```json
{
  "name": "新名称"
}
```

**响应 200:**

```json
{
  "id": "uuid",
  "name": "新名称"
}
```

---

### DELETE /api/v1/sessions/{session_id}

删除会话 (级联删除关联任务)。

**响应 204:** 无内容

---

## 回测

### POST /api/v1/auto_backtest

提交回测任务。支持自然语言描述或直接输入因子表达式。异步执行,立即返回 task_id。

**请求体:**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `prompt` | string | 是 | — | 自然语言描述或因子表达式 |
| `universe` | string | 否 | `hs300` | 股票池: `small_scale` / `hs300` / `csi500` |
| `start_date` | string | 否 | `2023-01-01` | 起始日期 YYYY-MM-DD |
| `end_date` | string | 否 | `2025-12-31` | 结束日期 YYYY-MM-DD |
| `n_groups` | int | 否 | `5` | 分组数量 (2~20) |
| `holding_period` | int | 否 | `5` | 持仓周期 (1~60 交易日) |
| `benchmark` | string | 否 | `hs300` | 基准: `hs300` / `zz500` / `sz50` |
| `session_id` | string | 否 | null | 关联会话 ID |
| `oos_enabled` | bool | 否 | `true` | 默认启用 OOS selection；显式 `false` 才走 legacy `auto_full` |
| `validation_stage` | string | 否 | `selection` | `selection` 只用 train+valid；`final` 才运行并暴露 test |

**请求示例:**

data-quality 默认会在元数据可用时过滤 ST/*ST、停牌、新股窗口和一字涨跌停，并报告复权收益不一致；
旧缓存或免费源缺字段时会在 `data_quality.warnings` 中标明降级。

```bash
curl -X POST http://localhost:8003/api/v1/auto_backtest \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "帮我测试一个20日动量因子",
    "universe": "hs300",
    "start_date": "2023-01-01",
    "end_date": "2025-12-31"
  }'
```

**响应 202:**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "pending"
}
```

**错误:** 429 (频率限制), 503 (任务已满), 400 (参数错误)

---

### GET /api/v1/tasks

分页查询当前用户的任务列表。

**查询参数:**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `page` | 1 | 页码 |
| `page_size` | 20 | 每页数量 (1~100) |
| `session_id` | — | 按会话过滤 |

**响应 200:**

```json
{
  "tasks": [
    {
      "task_id": "a1b2c3d4e5f6",
      "status": "completed",
      "params": { "...": "..." },
      "expression": "rank(close/ts_mean(close, 20))",
      "result": { "...": "..." }
    }
  ],
  "page": 1,
  "page_size": 20
}
```

---

### GET /api/v1/tasks/{task_id}

查询单个任务状态和结果。

**任务状态流转:**

```
pending → generating_expression → validating → fetching_data
  → backtesting → analyzing → generating_report → completed / failed
```

迭代任务:

```
pending → iterating → iteration_completed / failed
```

**完成响应:**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "completed",
  "expression": "rank(close/ts_mean(close, 20))",
  "result": {
    "report_url": "/api/v1/reports/backtest_report_20260321.html",
    "metrics": {
      "total_return": 0.156,
      "cagr": 0.052,
      "sharpe": 0.48,
      "sortino": 0.63,
      "max_drawdown": -0.182,
      "volatility": 0.185,
      "win_rate": 0.524,
      "profit_factor": 1.12
    },
    "backtest_summary": {
      "long_short_sharpe": 0.35,
      "long_short_annual": 0.043,
      "top_group_sharpe": 0.48,
      "monotonicity_score": 0.8,
      "spread": 0.00082,
      "ic_mean": 0.032,
      "rank_ic_mean": 0.038,
      "ic_ir": 0.45,
      "ic_win_rate": 0.56,
      "turnover": 0.42,
      "cost_adjusted": true,
      "cost_rate": 0.003,
      "total_cost_drag": 0.0156,
      "group_returns": {
        "0": { "group": "G1", "mean_return": 0.00021, "annual_return": 0.054, "sharpe": 0.35, "max_drawdown": -0.15 },
        "4": { "group": "G5", "mean_return": 0.00102, "annual_return": 0.293, "sharpe": 0.85, "max_drawdown": -0.12 }
      }
    },
    "anti_overfit": {
      "score": 75.0,
      "recommendation": "谨慎",
      "passed_count": 3,
      "total_count": 4,
      "tests": [
        { "name": "IC稳定性", "passed": true, "details": { "ic_mean": 0.032, "positive_rate": 0.58 } },
        { "name": "子样本压力", "passed": true, "details": { "consistency": 0.8 } },
        { "name": "安慰剂检验", "passed": true, "details": { "perm_pass": true, "decay_ok": true } },
        { "name": "半衰期估计", "passed": false, "details": { "half_life_days": 3.2 } }
      ]
    },
    "stock_factor_data": {
      "rebalance_date": "2025-12-15",
      "flipped": false,
      "total_stock_count": 300,
      "stocks": [
        { "stock_code": "sh.600519", "factor_value": 1.05, "factor_rank": 0.98, "group": 4, "group_label": "G5", "period_return": 0.12 }
      ]
    },
    "params": {
      "expression": "rank(close/ts_mean(close, 20))",
      "universe": "hs300",
      "start_date": "2023-01-01",
      "end_date": "2025-12-31",
      "n_groups": 5,
      "holding_period": 5,
      "benchmark": "hs300",
      "stock_count": 300
    },
    "llm": {
      "prompt": "帮我测试一个20日动量因子",
      "generated_expression": "rank(close/ts_mean(close, 20))"
    }
  }
}
```

**失败响应:**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "failed",
  "error": "因子表达式无效: Unknown column"
}
```

---

## 因子截面值

### POST /api/v1/factor_values

按因子表达式、股票池和日期区间计算每日全市场截面因子值。该接口返回原始因子得分，不执行分组回测、不生成报告，适合外部组合构建、因子库上传或 Agent 下游分析。

需要 Bearer Token。

**请求体:**

```json
{
  "expression": "rank(ts_mean(close/open, 10))",
  "universe": "csi500",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31"
}
```

字段说明：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `expression` | string | 是 | — | 因子表达式，最长 2000 字符 |
| `universe` | string | 否 | `csi500` | 股票池：`small_scale` / `hs300` / `csi500` / `csi1000` / `csi2000` |
| `start_date` | string | 否 | `end_date` 前 365 天 | 起始日期 YYYY-MM-DD |
| `end_date` | string | 否 | 今天 | 结束日期 YYYY-MM-DD |

日期跨度最大 750 天。服务会在 `start_date` 前额外取 260 天行情用于滚动表达式预热，但响应只包含目标区间内的数据。

**响应 200:**

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
        "sh.600000": 0.812345,
        "sz.000001": 0.7321
      },
      "count": 2
    }
  ]
}
```

**错误:** 400 (表达式为空/过长、日期格式错误、日期跨度过大、无行情数据), 401 (未认证)

---

## 策略框架

策略框架 API 使用 `StrategySpecV0` 作为 MVP 结构化输入。MVP 只支持
`a_share + single factor + rank_threshold + equal_weight + risk v0`。
当前仓库还包含 Post-MVP 能力：`StrategySpecV1`、多因子、top N、候选
SignalExport、策略持久化、模板、优化器和前端策略工作台。所有策略能力仍不提供
券商、账户、下单、API key 执行或实盘交易能力。

### GET /api/v1/strategy/markets

列出可用策略市场。无需认证。

**响应 200:**

```json
{
  "markets": [
    {
      "market": "a_share",
      "asset_class": "equity",
      "frequency": "daily"
    }
  ]
}
```

### GET /api/v1/strategy/data-fields

列出指定市场可用于策略因子表达式的数据字段。无需认证。

**查询参数:**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `market` | `a_share` | 市场标识；MVP 只支持 `a_share` |

**响应 200:**

```json
{
  "market": "a_share",
  "data_fields": [
    { "name": "close", "description": "收盘价" },
    { "name": "volume", "description": "成交量" }
  ]
}
```

**错误:** 404 (市场不支持)

### POST /api/v1/strategy/validate

校验 `StrategySpecV0`。成功时返回规范化后的 spec；失败时返回结构化错误。

**请求体:**

```json
{
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
}
```

**响应 200:**

```json
{
  "is_valid": true,
  "issues": [],
  "spec": { "schema_version": "strategy_spec/v0" }
}
```

**错误 400:**

```json
{
  "detail": {
    "is_valid": false,
    "issues": [
      {
        "code": "MARKET_UNSUPPORTED",
        "path": "market",
        "message": "Input should be 'a_share'",
        "hint": "MVP only supports market='a_share'."
      }
    ]
  }
}
```

### POST /api/v1/strategy/backtest

提交策略级异步回测任务。认证开启时仅登录用户或 API key 用户可提交；
anonymous 和 guest token 返回 401，且不会创建不可追踪策略任务。
`AUTH_DISABLED=true` 时使用 dev user 路径，允许本地开发提交。

**请求体:**

```json
{
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
}
```

**响应 202:**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "pending"
}
```

任务完成后，`GET /api/v1/tasks/{task_id}` 的 `result` 包含：

```json
{
  "strategy_result": {
    "metrics": { "sharpe": 0.8 },
    "latest_holdings": [],
    "risk_logs": []
  },
  "strategy_score": { "score": 70, "grade": "B" },
  "report_url": "/api/v1/reports/backtest_report_strategy.html"
}
```

`summary_json` 是服务端内部产物路径，不会出现在任务结果中。客户端应读取
`strategy_result` 摘要字段，并通过 `report_url` 访问 HTML 报告。

### Post-MVP Strategy Endpoints

Post-MVP adds versioned strategy extensions while keeping the same non-trading
boundary.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/strategy/templates` | List template summaries and governance bounds |
| `GET /api/v1/strategy/templates/{template_id}` | Read one template |
| `POST /api/v1/strategy/templates/{template_id}/instantiate` | Create a StrategySpec from a template |
| `POST /api/v1/strategy/export` | Export candidate rebalance signals from a strategy result; requires login/API key |
| `POST /api/v1/strategy/diagnose` | Return diagnosis taxonomy and suggested spec changes |
| `POST /api/v1/strategy/anti-overfit` | Run strategy-level anti-overfit summary checks |
| `POST /api/v1/strategy/rolling-validation` | Run strategy-level rolling validation summary |
| `POST /api/v1/strategy/optimize` | Optimize candidate weights under StrategySpec risk rules |
| `POST /api/v1/strategy/specs` | Persist an authenticated user's StrategySpec |
| `GET /api/v1/strategy/specs` | List saved StrategySpecs |
| `POST /api/v1/strategy/runs` | Persist a StrategyRun result |
| `GET /api/v1/strategy/runs` | List saved StrategyRuns |

Example template instantiation:

```bash
curl -X POST http://localhost:8003/api/v1/strategy/templates/momentum_top_n_v1/instantiate \
  -H "Content-Type: application/json" \
  -d '{"overrides": {"signal_rules.top_n": 10}}'
```

Example optimizer input:

```json
{
  "spec": { "schema_version": "strategy_spec/v1" },
  "signals": [
    { "trade_date": "2024-01-02", "stock_code": "A", "score": 10.0 },
    { "trade_date": "2024-01-02", "stock_code": "B", "score": 1.0 }
  ]
}
```

Signal export requires promotion-ready `validation_provenance`; missing or
research-only provenance returns 400. Successful responses contain candidate `target_weight` rows and a
non-live-trading notice. They do not contain `broker`, `account`, `order`,
`api_key`, or execution instructions.

---

## 实时推送 (SSE)

### GET /api/v1/tasks/{task_id}/stream

Server-Sent Events 实时推送任务状态变化。

**认证流程:** EventSource 不支持自定义 Header。认证开启时先用 Bearer Token 调用
`POST /api/v1/tasks/{task_id}/sse-ticket`，再把返回的一次性短期 ticket 放到
`/stream?ticket=<ticket>`。ticket 只能用于对应 task，验证后即消费。

**事件类型:**

| 事件 | 说明 |
|------|------|
| `update` | 任务状态变化,data 为完整任务 JSON |
| `done` | 任务终态 (completed/failed/iteration_completed) |
| `error` | 错误 (任务不存在/超时) |

**示例:**

```javascript
const ticketRes = await fetch(`/api/v1/tasks/${taskId}/sse-ticket`, {
  method: "POST",
  headers: { Authorization: `Bearer ${token}` },
});
const { ticket } = await ticketRes.json();
const es = new EventSource(`/api/v1/tasks/${taskId}/stream?ticket=${ticket}`);
es.addEventListener("update", (e) => {
  const task = JSON.parse(e.data);
  console.log(task.status);
});
es.addEventListener("done", (e) => {
  es.close();
});
```

**超时:** 默认 300 秒,可通过 `QUANTGPT_SSE_TIMEOUT` 配置。

---

## 迭代优化

### POST /api/v1/tasks/{task_id}/iterate

基于已完成的回测任务,AI 自动生成多个改进候选因子。

**请求体:**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `n_candidates` | int | 否 | `5` | 候选数量 (1~10) |
| `run_rolling_validation` | bool | 否 | `false` | 是否运行滚动验证 |

**响应 202:**

```json
{
  "task_id": "iter_b2c3d4e5f6",
  "status": "pending"
}
```

**迭代完成后查询 (GET /tasks/{iter_task_id}):**

```json
{
  "task_id": "iter_b2c3d4e5f6",
  "status": "iteration_completed",
  "task_type": "iteration",
  "parent_task_id": "a1b2c3d4e5f6",
  "candidates_done": 5,
  "candidates_total": 5,
  "candidates": [
    {
      "expression": "rank(ts_corr(close, volume, 20)) * rank(ts_delta(close, 10)/close)",
      "status": "success",
      "score": 62.3,
      "grade": "B",
      "component_scores": { "sharpe": 55.0, "monotonicity": 80.0, "..." : "..." },
      "backtest_summary": { "...": "..." },
      "anti_overfit": { "score": 50.0, "mode": "fast", "...": "..." },
      "report_metrics": { "...": "..." },
      "report_url": "/api/v1/reports/backtest_report_xxx.html"
    },
    {
      "expression": "bad_expression",
      "status": "failed",
      "error": "表达式验证失败: Unknown column"
    }
  ],
  "result": {
    "parent_task_id": "a1b2c3d4e5f6",
    "parent_expression": "rank(close/ts_mean(close, 20))",
    "parent_score": 35.9,
    "parent_grade": "D",
    "candidates": [ "..." ]
  }
}
```

**SSE 支持:** 通过 `/tasks/{iter_task_id}/stream` 实时获取迭代进度,`candidates_done` 字段逐步递增。

---

### POST /api/v1/tasks/{task_id}/select_candidate

选择一个迭代候选因子。该边界会检查 `validation_provenance`；普通迭代候选默认来自
`auto_full` 研究回测，状态为 `research_only`，必须先通过完整 `factor_validation/v1`
suite 后才能被选择为正式候选。

**请求体:**

```json
{
  "candidate_index": 0
}
```

**响应 200:**

```json
{
  "task_id": "iter_b2c3d4e5f6",
  "selected_index": 0,
  "expression": "rank(ts_corr(close, volume, 20)) * rank(ts_delta(close, 10)/close)",
  "score": 62.3,
  "grade": "B",
  "report_url": "/api/v1/reports/backtest_report_xxx.html",
  "report_metrics": { "...": "..." },
  "backtest_summary": { "...": "..." }
}
```

---

## 报告

### GET /api/v1/reports/{filename}

下载 HTML 报告 (QuantStats 格式)。需认证,只能访问自己的报告。

```bash
curl -H "Authorization: Bearer <token>" \
  http://localhost:8003/api/v1/reports/backtest_report_20260321.html \
  -o report.html
```

**响应:** HTML 文件 (Content-Type: text/html)

---

## 反馈

### POST /api/v1/feedback

提交问题反馈,支持截图。

**请求体:**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | 是 | 问题描述 (1~2000字) |
| `screenshot` | string | 否 | 截图 base64 (data:image/png;base64,...) 最大 5MB |
| `task_id` | string | 否 | 关联任务 ID |
| `page_url` | string | 否 | 当前页面 URL |
| `user_agent` | string | 否 | 浏览器 UA |

**响应 201:**

```json
{
  "id": "abc123def456",
  "status": "received",
  "webhook_sent": true
}
```

---

### GET /api/v1/feedback-screenshots/{feedback_id}

获取反馈截图 (PNG)。ID 不可猜测,无需认证。

---

## 管理后台

所有管理接口路径前缀: `/api/v1/admin`

### POST /api/v1/admin/login

管理员登录。

**请求体:**

```json
{
  "password": "admin_password"
}
```

**响应 200:**

```json
{
  "token": "admin_jwt_token"
}
```

---

### GET /api/v1/admin/overview

获取系统总览数据。

**响应 200:**

```json
{
  "total_users": 13,
  "total_tasks": 106,
  "success_rate": 0.81,
  "total_feedbacks": 5,
  "unresolved_feedbacks": 2,
  "status_distribution": { "completed": 72, "failed": 17, "iteration_completed": 17 },
  "daily_tasks_7d": [
    { "date": "2026-03-15", "count": 8 }
  ],
  "user_trend_30d": [
    { "date": "2026-03-01", "count": 2 }
  ]
}
```

---

### GET /api/v1/admin/users

用户列表 (分页)。

**查询参数:** `page`, `page_size`

---

### GET /api/v1/admin/tasks

任务列表 (分页,可按状态/用户过滤)。

**查询参数:** `page`, `page_size`, `status`, `user_id`

---

### GET /api/v1/admin/feedbacks

反馈列表 (分页)。

**查询参数:** `page`, `page_size`

---

### PATCH /api/v1/admin/feedbacks/{feedback_id}/resolve

标记反馈为已解决。

**响应 200:**

```json
{
  "id": "feedback_id",
  "resolved": true,
  "resolved_at": "2026-03-21T12:00:00"
}
```

---

## MCP Tools

QuantGPT 提供 MCP (Model Context Protocol) 服务,支持因子研究工具和 StrategySpec v0 策略工具。推荐本机 stdio 模式；如果暴露 HTTP MCP (`/mcp`, `/mcp-sse`)，认证开启时必须设置 `QUANTGPT_MCP_HTTP_TOKEN` 并由客户端发送 `Authorization: Bearer <token>`。

| Tool | 说明 |
|------|------|
| `list_operators` | 返回全部因子表达式算子及用法 |
| `list_universes` | 返回可用股票池和基准列表 |
| `validate_expression` | 验证表达式语法,返回 OK 或错误 |
| `run_backtest` | 执行回测,返回完整指标 + 反过拟合检测 + 报告路径 |
| `score_factor` | 执行回测并返回综合评分 (0-100, A/B/C/D) |
| `compute_factor_values` | 按股票池和日期区间输出每日截面因子值 |
| `diagnose_factor` | 诊断因子问题,推荐突变策略 (6种) |
| `run_anti_overfit` | 独立反过拟合检测 (4项测试) |
| `run_rolling_validation` | Walk-Forward 滚动验证 |
| `list_markets` | 返回策略框架支持的市场 |
| `list_data_fields` | 返回指定市场可用于策略因子表达式的数据字段 |
| `validate_strategy_spec` | 校验 `StrategySpecV0`，失败时返回 `error_code` 和 `hint` |
| `run_strategy_backtest` | 运行策略级回测，返回收益、目标权重和风控日志 |
| `score_strategy` | 根据策略回测结果计算策略评分 |
| `generate_strategy_report` | 根据策略回测结果生成 HTML 报告和 summary JSON |

candidate / submit / export 边界要求 `factor_validation/v1` 晋级证明：data-quality gate、
train/valid/test、rolling window、placebo test、time-shift test 必须全部通过。Agent/结论型
因子回测默认使用 `oos_enabled=true` 与 `validation_stage="selection"`：train 定方向/参数，
valid 选候选，test 指标 withheld；只有显式 `validation_stage="final"` 才运行并暴露 test 作为
frozen candidate 的最终验收。普通 `auto_full` 回测结果会标记为 `research_only`，不能直接选择为
候选、提交或导出。

### 配置 (.mcp.json)

```json
{
  "mcpServers": {
    "quantgpt": {
      "command": "python",
      "args": ["-m", "quantgpt"],
      "cwd": "/path/to/quantgpt"
    }
  }
}
```

---

## 健康检查

### GET /api/v1/health

无需认证。

**响应 200:**

```json
{
  "status": "ok",
  "active_tasks": 2,
  "total_tasks": 106
}
```

---

## 错误码

| HTTP 状态码 | 说明 |
|------------|------|
| 400 | 参数错误 (日期格式、表达式无效等) |
| 401 | 未认证或 Token 过期 |
| 403 | 权限不足 (管理接口) |
| 404 | 资源不存在 |
| 429 | 频率限制 (每分钟 10 次) |
| 503 | 服务繁忙 (并发任务已满) |

**错误响应格式:**

```json
{
  "detail": "错误描述信息"
}
```

---

## 股票池与基准

### 股票池

| 名称 | 说明 |
|------|------|
| `small_scale` | 5 只蓝筹 (茅台、平安、五粮液、美的、招行),快速测试 |
| `hs300` | 沪深300成分股,动态获取 |
| `csi500` | 中证500成分股,动态获取 |

### 基准指数

| 名称 | 说明 |
|------|------|
| `hs300` | 沪深300指数 |
| `zz500` | 中证500指数 |
| `sz50` | 上证50指数 |

---

## 因子表达式语法

### 支持的算子

**截面函数:** `rank(expr)`, `zscore(expr)`, `sign(expr)`, `log(expr)`, `abs(expr)`, `scale(expr)`

**时序函数:** `ts_mean(col,N)`, `ts_std(col,N)`, `ts_sum(col,N)`, `ts_max(col,N)`, `ts_min(col,N)`, `ts_shift(col,N)`, `ts_delta(col,N)`, `ts_rank(col,N)`, `ts_argmax(col,N)`, `ts_argmin(col,N)`, `decay_linear(col,N)`, `product(col,N)`

**双列时序:** `ts_corr(col1,col2,N)`, `ts_cov(col1,col2,N)`

**非线性:** `power(base,exp)`, `sign_power(base,exp)`, `tanh(expr)`, `sigmoid(expr)`, `exp(expr)`, `sqrt(expr)`

**条件:** `max(a,b)`, `min(a,b)`, `where(cond,true_val,false_val)`, `clip(expr,lower,upper)`

**算术:** `+`, `-`, `*`, `/`, `^`

**比较:** `>`, `<`, `>=`, `<=`, `==`, `!=`

**逻辑:** `and`, `or` (用于 where 条件)

**可用列:** `open`, `high`, `low`, `close`, `volume`, `amount`, `pct_change`

**特殊变量:** `vwap`, `returns`, `adv{N}` (如 adv20), `cap`

**别名:** `delta`=ts_delta, `delay`=ts_shift, `correlation`=ts_corr, `covariance`=ts_cov

### 示例

```
动量:     rank(close/ts_mean(close, 20))
反转:     rank(-1 * ts_delta(close, 5) / ts_shift(close, 5))
波动率:   rank(-1 * ts_std(returns, 20))
量价相关: rank(ts_corr(close, volume, 10))
复合:     sign_power(rank(volume/adv20), 2) * rank((close-vwap)/close)
```

---

## 典型调用流程

### 因子研究流程

```
1. POST /auth/send-code          → 发送验证码
2. POST /auth/verify-code        → 获取 Token
3. POST /sessions                → 创建会话
4. POST /auto_backtest           → 提交回测
5. GET  /tasks/{id}/stream       → SSE 监听进度
6. GET  /tasks/{id}              → 获取完整结果
7. GET  /reports/{filename}      → 下载报告
8. POST /tasks/{id}/iterate      → AI 迭代优化
9. POST /tasks/{id}/select_candidate → 选择候选（必须已有完整 validation_provenance）
```

### 策略 MVP 流程

```
1. GET  /strategy/markets        → 确认可用市场
2. GET  /strategy/data-fields    → 确认可用字段
3. POST /strategy/validate       → 校验 StrategySpecV0
4. POST /strategy/backtest       → 提交策略回测任务
5. GET  /tasks/{id}              → 读取 strategy_result / score / report_url
6. GET  /reports/{filename}      → 查看策略 HTML 报告
```
