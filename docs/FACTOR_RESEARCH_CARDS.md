# Factor research cards

`quantgpt.research.cards.build_factor_research_card` builds a finite JSON artifact
from one registered definition/configuration and the complete internal
`run_factor_backtest` result. It does not retrieve data or run new expressions.
The service must authorize the project, freeze the definition, register the trial
and reserve any final window before computing that result. Store the returned
payload with the existing project-scoped `write_artifact` service.

The card records evaluation/definition/content identities, source manifests,
field contracts, universe and split, raw-direction IC and horizon, pointwise
moving-block intervals, gross/net ledger groups, charged fees, realized turnover,
year-by-year return stability and simulation scope. A constant or missing metric
is not converted into passing evidence. Empty observations and insufficient
independent blocks have explicit conclusions; an interval crossing zero is
reported as not significant at the pointwise 95% level. No composite score or
automatic acceptance is produced. Intervals remain unadjusted for selection.
The trial count is captured when the immutable card is created; a later
validation decision must query the current project ledger rather than assume
that historical count includes subsequently observed trials.

`EvaluationEvidence` binds a definition/configuration to its runner output.
`compare_evaluation_evidence` requires identical data, scope, universe, engine,
costs and split, a matching frozen baseline hash, identical return sessions and
identical signal security/session keys. It computes signed daily cross-sectional
signal rank correlations, return correlation and a paired net return difference.
The baseline must be a portfolio baseline frozen before selection: the project
service must resolve its hash from an immutable audit record, not trust a hash
supplied by an arbitrary client. Standalone factor performance is not renamed
portfolio incremental contribution.

`compare_registered_variants` accepts already observed, separately registered
horizon or cost evaluations. Each retains its evaluation hash and counts as a
trial. Horizon variants may change the label/purge horizon and rebalance period;
cost variants may change fee/slippage rates. All other inputs must match. This
function does not rerun data or evade final-window exposure controls.

Missing risk exposures, a registered economic hypothesis, library references,
portfolio baseline or extra scenarios remain visibly unavailable. The starter
momentum, reversal, liquidity and book-value examples are hypothesis templates,
not pre-registered experiments or observed effects. The book-value template is
blocked for the free IBM demonstration because equivalent PIT book equity and
market-cap fields are not yet supplied there.

Current status: **in_review / partial P15**. The card and comparison contracts
have offline coverage, including an actual Python backtest/ledger path; the test
data are synthetic. Full P15 acceptance still requires project service audit
integration for comparison execution, PIT risk exposures, and complete real frozen price/financial
samples. This does not establish G3 or a validated investment result.

The pre-registration service is now implemented in `research/hypotheses.py`.
`register_hypothesis` uses a project revision compare-and-swap, appends one
content-addressed audit event, and rejects changes or late registration after any
trial for the definition exists. Identical retries remain valid after observation.
`get_hypothesis` checks live membership and payload integrity. Optional baseline
references must belong to the same project, match the stored immutable evaluation
and have an observed result. Evaluation registration uses the same revision lock
so concurrent uncommitted trials cannot bypass the pre-registration order.
Cards consume this server-resolved audit record through `hypothesis_registration`.
This covers hypothesis/baseline registration; comparison execution, PIT risk
exposures and complete real-market samples remain separate missing requirements.

Validation on Windows CPython 3.12.14:

```powershell
.venv312/Scripts/python.exe -m pytest tests/test_research_cards.py tests/test_research_hypotheses.py -q --basetemp=test-results/p15hypotheses -o cache_dir=test-results/pytest-cache
# 19 passed, including concurrent registration and audit tamper/revocation
.venv312/Scripts/python.exe -m ruff check quantgpt/research/cards.py tests/test_research_cards.py
.venv312/Scripts/python.exe -m pyright quantgpt/research/cards.py
# Ruff passed; Pyright 0 errors
```
