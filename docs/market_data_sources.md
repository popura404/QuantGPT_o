# Market Data Sources

## US research adapter

`market=us` / `us_equity` 默认使用 Alpaca 历史 SIP 原始日线，SEC Company Facts 提供按决策时刻对齐的发行人财务数据。证券身份、ticker 生效区间和发行人 CIK 来自显式清单。通过 `python -m scripts.freeze_us_research` 将行情及所选基本面冻结，再沿用共享研究的快照发现、登记和窗口规划入口。配置及操作统一见[多代理研究指南](agent_safe_workflow.md#美股行情与基本面准备)。Alpha Vantage compact 保留为显式选择的探索入口。

XNYS 交易日历、Alpaca 和 SEC 数据契约使用模拟请求验收。缺少证券主数据、完整公司行动和退市记录、历史成员或可验证基准时，适配器返回 `research_only` 和具体缺项。快照覆盖计算使用的列、财务版本和日历属性，并校验清单身份和文件内容。使用原始价格的现金账本拒绝显式前复权或后复权价格，避免重复计算公司行动。[数据接口与验证边界](US_FREE_DATA_DECISION.md)

Purpose: describe the source order and provenance fields used by market data.

Default A-share OHLCV source order:

```text
local parquet cache -> baostock free fetch -> optional rqdatac fetch
```

`QUANTGPT_CACHE_ONLY=true` disables remote fetches. Cached misses should fail or
return empty data rather than silently using remote data.

`QUANTGPT_BAOSTOCK_TIMEOUT` bounds baostock socket waits in seconds; `0`
disables the timeout. The default is `20`.

MCP factor-heavy tools (`run_backtest`, `score_factor`, `compute_factor_values`,
`run_anti_overfit`, `run_rolling_validation`) default to local-cache-only data
access. Passing `allow_remote_fetch=true` permits remote fills only after a
planning check. If the call would need to fill more than
`QUANTGPT_MCP_REMOTE_FETCH_STOCK_LIMIT` stock parquet files, the tool returns
`REMOTE_PREFETCH_REQUIRED` and a suggested prewarm command instead of starting a
large remote batch.

The same five tools support asynchronous MCP execution with `submit_only=true`.
The initial response contains a `task_id`; use `get_mcp_task_status` for
progress fields (`status`, `progress`, `progress_message`, `stage`) and
`cancel_mcp_task` to request cooperative cancellation. Cancellation is checked
between cache scans, per-stock baostock fetches, rqdatac chunks, fundamental
fetches, and CPU-result polling. It cannot forcibly terminate a single
in-flight baostock/rqdatac/socket call; the practical upper bound is the current
provider call plus `QUANTGPT_BAOSTOCK_TIMEOUT` where applicable.

Single-stock research should use `get_stock_history`, which reads only
`data/stocks/<market>_<code>.parquet`, or `check_market_cache` to inspect the
monthly universe cache and stock coverage. These diagnostic tools never fetch
remote data.

Result metadata records the artifact source:

```json
{
  "data_source": "baostock",
  "data_snapshot_id": "ds_...",
  "data_source_metadata": {
    "source_kind": "remote_fetch_snapshot",
    "source_metadata": {
      "artifact": "ohlcv",
      "source_counts": {"baostock:free_source_fetch": 3}
    }
  }
}
```

`quantgpt/market_data_providers.py` exposes a lightweight optional-provider
registry. It lists free/cache providers and paid-provider placeholders, reports
credential and SDK availability, and returns structured `PROVIDER_UNAVAILABLE`
payloads instead of importing paid SDKs as hard dependencies.

Optional provider credentials:

- `RQDATAC_USERNAME`, `RQDATAC_PASSWORD`
- `TUSHARE_TOKEN`
- `WIND_USERNAME`, `WIND_PASSWORD`
- `CHOICE_USERNAME`, `CHOICE_PASSWORD`
- `JQDATA_USERNAME`, `JQDATA_PASSWORD`

Safe usage note: optional paid providers must never become hard runtime
dependencies for free-source backtests or tests.
