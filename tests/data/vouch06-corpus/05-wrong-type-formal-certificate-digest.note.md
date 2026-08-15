# Wrong-typed formal certificate digest

Base: a minimal schema-valid qualified release in `retired`.

Mutation: adds `formal_certificate_digest` as a number instead of a non-empty string.

Required catch: the non-empty-string constraint must reject the release.
