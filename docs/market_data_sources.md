# Market Data Sources

## US research adapter

`market=us` 提供显式固定cohort的免费 Alpha Vantage OHLCV演示；`USDataProvider` 是可替换的许可供应商接口。XNYS会话日历与 SEC companyfacts PIT 容器有离线契约测试；数据缺少主证券库、完整公司行动/退市、成员历史或可验证基准时返回research_only能力限制。来源与实际短样本结果见 [免费数据决策](US_FREE_DATA_DECISION.md)。完整快照覆盖全部消费列及公司行动/日历属性，并校验manifest身份与文件内容。raw现金账本拒绝显式qfq/hfq价格，不能双计公司行动。

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
