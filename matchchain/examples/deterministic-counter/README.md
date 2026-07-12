# Deterministic Counter Example

The application state is `{value:number}`. Payload `{delta:1}` increments by one. A transition verifier rejects other deltas and recomputes the state hash before append. Transport and signature adapter are selected by the embedding application.
