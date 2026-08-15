# Bare unit-deviation binding

Base: a complete minimal ledger with one schema-valid unit deviation bound as
`unit-a@1.0.0`.

Mutation: changes the deviation `unit` binding to the bare name `unit-a`.

Required catch: kit-3 unit deviations must bind a resolved `NAME@VERSION`, never a
bare name.
