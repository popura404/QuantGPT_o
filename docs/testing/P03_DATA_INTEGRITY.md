# P03 follow-up validation

Status: contract_verified for the offline regressions listed here. Real provider
coverage, licensed historical universes and vintage completeness remain separate
external integration requirements.

Validation on Windows / CPython 3.12.14:

```powershell
.venv312/Scripts/python.exe -m pytest tests/test_market_cache_integrity.py tests/test_research_data_integrity.py tests/test_data_quality.py tests/test_fundamental_data.py tests/test_market_data.py tests/test_market_data_providers.py tests/test_data_snapshots.py tests/test_us_data.py -q --basetemp=test-results/p03final -o cache_dir=test-results/pytest-cache
# 122 passed
.venv312/Scripts/python.exe -m ruff check quantgpt/data_quality.py quantgpt/data_snapshots.py quantgpt/pit_data.py quantgpt/fundamental_data.py quantgpt/market_data.py tests/test_market_cache_integrity.py tests/test_research_data_integrity.py
# All checks passed
.venv312/Scripts/python.exe -m pyright quantgpt/data_quality.py quantgpt/data_snapshots.py quantgpt/pit_data.py quantgpt/fundamental_data.py quantgpt/market_data.py
# 0 errors
```

The market cache publishes each Parquet file by an atomic same-directory replace.
An interprocess lock serializes fetches, and a second waiting fetch checks the
fresh cache again. Cache coverage checks the XSHG exchange calendar, including
missing internal sessions and holidays. It never applies a five-day tolerance.
Incremental concatenation requires matching provider/feed/raw-price/version
metadata. Adjusted or unknown bases require a complete replacement that includes
every prior cached session. Existing caches are retained if the replacement is
incomplete or a write fails. Calendar dates outside the installed calendar's
supported bounds cannot be certified. Delisting and legitimate suspension gaps
still require independent event data; incomplete coverage remains incomplete.

Frame snapshot identity includes every column and calculation-relevant metadata:
corporate actions, calendar, adjustment, provenance, capability status and universe
membership. Replay verifies both the saved bytes and reconstructed manifest
identity and restores those attributes. A per-snapshot file lock and a manifest
published last avoid exposing partially written snapshots. Mutable provider
caches are not historical vintage stores.

Financial alignment selects the latest period known at a decision and then the
latest publication of that period. A later restatement of an older period cannot
replace a newer known period. Date-only publication is usable from the following
calendar day. The generic PIT API requires explicit timezone timestamps.
Quarterly profit/ROE is no longer used as a proxy for book equity; unsupported
PB/PS/ROA/BPS/NAV fallback derivations return structured capability errors. PE
requires an explicit raw price basis and TTM EPS. This does not establish the
vendor's historical filing-vintage completeness. Existing legacy names remain
discoverable for compatible providers with actual equivalent fields.

All regression panels are explicitly synthetic. Linux CI and live A-share
provider refreshes were not run as part of this validation.
