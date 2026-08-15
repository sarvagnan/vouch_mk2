# Empty protective-closure trigger

Base: a minimal schema-valid qualified release in `retired`.

Mutation: changes `closure_record.trigger` to the empty string.

Required catch: the non-empty-string constraint must reject the record.
