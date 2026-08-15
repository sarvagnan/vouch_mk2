# Missing closure trigger kind

Base: a complete, schema-valid minimal qualified protective-closure ledger.

Mutation: removes the required `closure_record.trigger_kind` property.

Required catch: the closed closure-record shape must require the enum field even
when the free-text `trigger` is present and non-empty.
