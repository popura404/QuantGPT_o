# Free US data interfaces and verification

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
remote US fetch. The MCP factor-values tool forwards `market`, `backend` and
remote-fetch permission through the shared service. REST and MCP preserve
structured capability errors, including retryability and the next action.

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

## Verification

Run `python -m pytest tests/test_us_data.py -q` for the adapter contracts.
Provider HTTP calls use mocks. The tests exercise cancellation, quota errors,
response fingerprints, missing sessions, security mapping dates, SEC revisions,
reported units and pagination.

For an external check, record the source, request and response hashes, UTC
observation time, requested dates, returned sessions and gaps. Keep licensed
data and provider credentials outside Git. SEC requests require an
operator-supplied contact User-Agent.

Formal market evidence requires historical security and membership records,
delisting settlements, verified split and dividend treatment, an immutable
snapshot with retention rights and the applicable point-in-time fields. The
evaluation records the available evidence and any missing prerequisites.
