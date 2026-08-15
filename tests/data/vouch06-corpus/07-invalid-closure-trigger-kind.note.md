# Invalid closure trigger kind

Base: a complete, schema-valid minimal qualified protective-closure ledger.

Mutation: changes `closure_record.trigger_kind` to `manual`, outside the closed
`amendment | operator | supersession` vocabulary.

Required catch: the closure-record enum must reject an origin classification that
free text could otherwise imitate.
