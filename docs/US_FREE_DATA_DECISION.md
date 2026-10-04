# Free US data decision and verification boundary

Decision date: 2026-10-05 (Asia/Shanghai). User direction: complete offline
contracts first, use free data for a small verification, retain paid-provider
integration points. No subscription was purchased and no private credential was
read. P13/P14 are **partially implemented / in_review**, not G3 verified.

## Selected interfaces

| Source | Implemented | Explicit limitations |
| --- | --- | --- |
| Alpha Vantage official daily compact endpoint | Raw USD daily OHLCV, explicit security mapping, source/request hashes, missing-session report | Limited history; no verified actions, historical membership, delisting settlement or security master |
| SEC companyfacts and submissions | Exact taxonomy/unit fields, filing accession vintages, conservative availability, safe historical filing pages | Issuer-level data; custom taxonomy/segment coverage and split-adjusted share history incomplete |
| exchange_calendars XNYS | Versioned regular sessions, holidays, half-days and UTC/DST timestamps | Software calendar version is recorded; unusual future exchange decisions still require updates |
| Licensed provider | `USDataProvider` protocol retained for prices, events and historical universe | No paid adapter is connected or claimed available |

Alpha Vantage documents daily data as raw OHLCV, with the compact response
limited to 100 observations; full history and daily adjusted data require
premium access. The public IBM demo is a limited connectivity sample.
[Official API documentation](https://www.alphavantage.co/documentation/).
Its terms restrict ordinary access to personal, noncommercial use unless agreed
otherwise. This implementation makes no commercial redistribution or perpetual
snapshot-retention entitlement claim.
[Provider terms](https://www.alphavantage.co/terms_of_service/).

SEC API data needs no API key. Company facts retain reported units and periods;
submission metadata declares additional history files. The adapter sends an
operator-supplied identifying User-Agent, uses a shared per-process 5 requests/s
gate, and never fabricates a real contact address. Distributed deployments must
share their quota across workers.
[SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[SEC developer access policy](https://www.sec.gov/about/developer-resources).

The calendar dependency is locked at 4.13.2 in both platforms. Fixtures verify
2024 DST transitions, July 4 closure, Thanksgiving Friday half-day, and the
2025-01-09 exceptional closure. The library is a schedule implementation, not a
market-data license.
[Calendar project](https://github.com/gerrymanoim/exchange_calendars),
[NYSE hours/calendar](https://www.nyse.com/markets/hours-calendars).

## Public contracts and integration

`quantgpt/us_data` never equates ticker or issuer CIK with a permanent share-class
security ID. `SecurityMapping` is explicit and has effective dates; out-of-range
requests fail. The built-in `ibm_demo` cohort uses a clearly named demo identity.
It is not a historical membership series. ETF/ADR/OTC and non-USD mappings fail.

`market="us"` is registered in the common strategy adapter registry. Its fields
have unit, availability policy, period, vintage, version and `research_only`
status. Benchmark is `none`; an empty benchmark is labelled unavailable, never
claimed as total return. Corporate actions and historical universes produce
structured capability failures rather than empty successful event histories.

The shared factor-values service and REST request support `market`/`backend`.
A-share defaults are preserved. US factor values use the same adapter as strategy
research; the result propagates `research_only`, capability blockers and provenance.
Warmup uses expression AST requirements and real XNYS sessions. Recursive warmup
without a frozen initialization is blocked. Cache-only requests cannot initiate a
remote US fetch. MCP callers should forward `market`, `backend` and remote-fetch
permission through their task wrapper; the MCP implementation is integrated by
the service owner separately.

SEC `net_income`, assets, liabilities, reported common shares and period diluted
EPS retain exact tags/units. Missing fields fail; no close×volume, price, current
ratio, or generic revenue proxy is substituted. `available_at` is midnight New
York on the calendar day following the filing date. This conservative date-only
rule excludes same-day trading, including after-hours releases. All revisions
are retained; selection chooses the latest eligible accession for each exact
issuer/tag/unit/period. Four-quarter TTM rejects annual/YTD overlap and mixed
units; no automatic conversion of cumulative YTD into quarters is claimed.
Reported shares are not yet suitable for split-consistent historical market cap,
and current SIC is not presented as historical industry.

## Evidence and status

`tests/test_us_data.py` plus existing factor/adapter regressions: 24 passed on
Python 3.12.14. HTTP is mocked only at the provider boundary; the REST test invokes
the real factor service, registered adapter and expression evaluator with a test
database/authentication. JSON fixtures are synthetic and labelled accordingly.
Cancellation, quota failure without blind retry, response fingerprints, internal
holes, effective ticker dates, SEC revisions/units and bounded safe pagination
are covered. Provider keys are absent from persisted metadata and error messages.

An actual public IBM demo request returned 10 sessions for 2026-09-19 through
2026-10-03 without missing sessions. Evidence is in
[us-free-external-sample.json](testing/us-free-external-sample.json): response hash,
request hash, UTC timestamp and row count, with no raw vendor prices committed.
`external_integration=limited_demo_response_verified` does not mean full P13
acceptance. SEC actual external requests remain `not_run` because a real contact
User-Agent was not configured. Research performance was not evaluated.

Still required for P13/P14 completion: verified rename and delisting/cash-merger
terminal examples, split/dividend accounting samples, licensed immutable replay,
historical universe/security master, trustworthy publication/share-adjustment
history, PIT industry, financial daily enrichment and the complete REST/MCP
backtest path. The free data boundary therefore remains `research_only` and
cannot be used as trusted promotion or export evidence.
