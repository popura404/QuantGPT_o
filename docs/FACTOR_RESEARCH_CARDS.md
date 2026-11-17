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

Project evaluations store a `factor_research_card/v2` payload with a fixed
`card_hash`. It includes the definition's pre-registered economic hypothesis
when one exists. Its `statistics.observed_project_trials` is null and
`trial_count_source` points to `artifact_research_context`. Archived cards
retain their original payload, hash and any count recorded at creation.

When reading a card with `get_research_artifact`, use the top-level
`research_context`. Its `direction_hypotheses` lists each direction that
proposed this evaluation, including its hypothesis, actor and proposal task
IDs. Two directions can share one evaluation and both receive credit.
`observed_project_trials` gives the trial denominator. A `trial_count_scope`
of `live_project` uses the project count at read time. For a card included in
a frozen round manifest, `frozen_round` uses the sealed count and returns the
corresponding `trial_manifest_hash`.

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

The project-level `compare_research_strategies_to_baseline` tool compares an
augmented selection strategy with the baseline registered for its round. The
service stores the comparison as an artifact. Point-in-time risk exposures and
real price and financial coverage depend on the registered dataset.
[Project research API](RESEARCH_API.md)

The pre-registration service is now implemented in `research/hypotheses.py`.
`register_hypothesis` uses a project revision compare-and-swap, appends one
content-addressed audit event, and rejects changes or late registration after any
trial for the definition exists. Identical retries remain valid after observation.
`get_hypothesis` checks live membership and payload integrity. Optional baseline
references must belong to the same project, match the stored immutable evaluation
and have an observed result. Evaluation registration uses the same revision lock
so concurrent uncommitted trials cannot bypass the pre-registration order.
Cards consume this server-resolved audit record through `hypothesis_registration`.

Run the card and pre-registration checks from the repository root on Windows with CPython 3.12.

```powershell
.venv312/Scripts/python.exe -m pytest tests/test_research_cards.py tests/test_research_hypotheses.py -q
.venv312/Scripts/python.exe -m ruff check quantgpt/research/cards.py tests/test_research_cards.py
.venv312/Scripts/python.exe -m pyright quantgpt/research/cards.py
```
