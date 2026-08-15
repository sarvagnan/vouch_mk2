# Wrong-typed outcome deviation

Base: a minimal schema-valid qualified release in `retired`.

Mutation: adds `outcome_deviations` with a numeric item instead of a string id.

Required catch: the string-array item constraint must reject the release.
