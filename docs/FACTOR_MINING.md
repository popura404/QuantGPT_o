# Factor Mining Methodology

Use the [multi-agent research guide](agent_safe_workflow.md) to open a project round, submit candidates, count trials, select a combination and evaluate the frozen holdout. This document covers how to design hypotheses and interpret measurements during development.

## Design a hypothesis

Describe the proposed economic mechanism, the universe in which it should apply, and the horizon over which it can be tested. Record the expected direction before observing the candidate's performance. Check the frozen panel for the required fields, their units, when they became available and how much prior history the expression needs.

Choose a baseline and vary one feature at a time, such as the lookback, scaling or grouping. A parameter change on an existing structure can be a useful comparison. Record its relationship to the original candidate so the project can account for both observed trials.

## Expression syntax

Use `list_operators` for the installed parser and `validate_expression` to check syntax. The following examples illustrate cross-sectional ranking, time-series averaging and conditional selection.

```text
rank(ts_delta(close, 5) / ts_shift(close, 5))
rank(close / ts_mean(close, 10))
where(volume > ts_mean(volume, 20), rank(close / vwap), rank(ts_delta(close, 5)))
```

In the local parser, `adv20` calculates the 20-observation rolling mean of `volume` for each security. Its unit follows the volume field. For a value traded in currency, check whether the snapshot provides a compatible traded-amount field. Remote WQ BRAIN simulation uses the platform's own field and operator conventions.

## Compare results

Compare the candidate and baseline on the same snapshot, window, horizon, costs and rebalance schedule. Record net returns, turnover, Sharpe, IC, coverage and the change from baseline where those measurements are available. A large score with few observations or sparse coverage merits examination before choosing a combination.

For the standalone `score_factor` tool's default OOS mode, A starts at 80, B at 65 and C at 50. These grades describe local diagnostic results. Formal WQ BRAIN submission uses the submission preflight decision, which also supports an explicitly recorded override. The shared project's final validation and export decisions come from its frozen run.

A second reviewer can examine the evidence and assumptions. The project's trial ledger and validation report supply the outcomes used for final selection and export.

## Record observations

Keep the hypothesis, configuration, evaluation references, baseline comparison and the reason for advancing or rejecting each candidate. An interrupted or unauthenticated request needs a task or transport diagnosis before drawing a conclusion about the formula. Local notes can use the [knowledge note template](knowledge/INDEX.md).

The legacy `scripts/factor_miner.py` helper sends HTTP requests without authentication headers. Under the default authenticated deployment, its batch submission can receive HTTP 401 and yield no result. For shared research, submit through the authenticated project workflow and inspect the returned task state. [Multi-agent research guide](agent_safe_workflow.md)
