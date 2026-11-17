# Free US data interfaces and verification

## Selected interfaces

| Source | Implemented | Explicit limitations |
| --- | --- | --- |
| Alpaca historical SIP, default US price source | Paginated raw USD daily OHLCV, explicit security mapping, source/request hashes, missing-session report | Credentials and SIP entitlement required; historical revisions unknown; no verified actions, historical membership, delisting settlement or security master |
| SEC companyfacts and submissions | Exact taxonomy/unit fields, filing accession vintages, conservative availability, safe historical filing pages | Issuer-level data; custom taxonomy/segment coverage and split-adjusted share history incomplete |
| Alpha Vantage official daily compact endpoint, explicit legacy option | Raw USD daily OHLCV and named IBM connectivity demo | Limited history; no verified actions, historical membership, delisting settlement or security master |
| exchange_calendars XNYS | Versioned regular sessions, holidays, half-days and UTC/DST timestamps | Software calendar version is recorded; unusual future exchange decisions still require updates |
| Additional providers | `USDataProvider` protocol retained for prices, events and historical universe | No additional adapter or provider entitlement is implied |

Alpaca requests use `feed=sip`, `adjustment=raw`, `timeframe=1Day` and `asof=-`.
The caller supplies ticker identity intervals. Daily bars aggregate the New York
calendar day, including extended-hours volume. This integration conservatively
sets price availability and `decision_at` to 00:15 New York the following day,
before the next exchange-session open. It retains the response observed during
collection and does not claim the vendor's original historical publication vintage.
[Alpaca market-data rules](https://docs.alpaca.markets/us/docs/market-data-faq).
Credentials use `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`; entitlement failures
remain explicit and do not switch the request to IEX.

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
requests fail. The default Alpaca adapter requires a caller-supplied cohort.
The built-in `ibm_demo` cohort is available with the explicit Alpha Vantage
option and uses a clearly named demo identity. Neither is a historical
membership series. ETF/ADR/OTC and non-USD mappings fail.

Use `python -m scripts.freeze_us_research` from the repository root to prepare
Alpaca prices and SEC fields before opening a shared research round. The command
caches source responses, publishes an immutable snapshot, and returns its scope
and field catalog for the existing snapshot registration tools. Configuration,
explicit CIK mappings and recovery steps belong to the
[multi-agent operations guide](agent_safe_workflow.md#美股行情与基本面准备).

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

SEC `total_assets` and `total_liabilities` are the default fields. Optional
`reported_common_shares`, `net_income` and `diluted_eps_period` retain exact
tags/units. Net income and diluted EPS require explicit discrete 60–120-day
quarters. Missing taxonomy or unit fields fail; dates before an eligible filing
retain null observations. `available_at` is midnight New
York on the calendar day following the filing date. This conservative date-only
rule excludes same-day trading, including after-hours releases. All revisions
are retained; selection chooses the latest eligible accession for each exact
issuer/tag/unit/period. Four-quarter TTM rejects annual/YTD overlap and mixed
units; no automatic conversion of cumulative YTD into quarters is claimed.
Reported shares are not yet suitable for split-consistent historical market cap,
and current SIC is not presented as historical industry.

## Verification

Run `python -m pytest tests/test_us_data.py tests/test_alpaca_data.py tests/test_sec_fundamentals.py -q`
for the provider and adapter contracts.
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
