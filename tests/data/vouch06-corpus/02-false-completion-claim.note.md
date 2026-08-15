# False no-completion statement

Base: a minimal schema-valid qualified release in `retired`.

Mutation: changes `closure_record.no_completion_claim` from true to false.

Required catch: the field's `const: true` constraint must reject the record.
