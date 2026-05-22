# External Bot Integration Boundary

Purpose: state what external systems may and may not infer from QuantGPT output.

QuantGPT may export candidate signal rows with `target_weight`, `rank`, `score`,
`risk_constraints`, `rebalance_frequency`, and `as_of`.

External systems own all order intent:

- current positions and cash checks
- lot-size conversion
- tradeability checks at execution time
- manual approval
- broker API authentication
- order creation, cancellation, and execution reports

QuantGPT must not emit broker credentials, account identifiers, order IDs, order
types, order prices, order volumes, or submit-order instructions.

Safe usage note: WQ BRAIN submit is a guarded research-platform action, not
broker execution. It remains behind local preflight or explicit override.
