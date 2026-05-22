# Market Data Sources

Purpose: describe the source order and provenance fields used by market data.

Default A-share OHLCV source order:

```text
local parquet cache -> baostock free fetch -> optional rqdatac fetch
```

`QUANTGPT_CACHE_ONLY=true` disables remote fetches. Cached misses should fail or
return empty data rather than silently using remote data.

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
