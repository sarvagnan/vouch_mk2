# formal-memory@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** Sol, who may not edit this file,
`tests/test_formal_memory.py` or `packs/formal-memory-1.effects.json`.
**Decision:** D-42 (settles `vouch_mk2-7dr`).
**Effects:** none. No store action and no state, event, object or certificate format changes
(`packs/formal-memory-1.effects.json` declares an empty list).
**Pack/2** (DEV-fm-01): pack/1 had `run_formal` carry each relation's digest on `UniverseRun`, but
the formal layer is pure (D-1's import contract forbids `hashlib`, `json` and the store there), so
the digest could only be attached by weakening that contract. Pack/2 keeps the formal layer pure:
the verification layer remembers the digests of relation objects it has already hashed.
**Pack/3** (DEV-fm-02), from Astra's review: the Promise said "at most once per process" although
the memo is bounded; the supported inputs were not stated; R5 did not catch an unbounded memo and
R7 did not pin the whole certificate.
**Pack/4** (DEV-fm-03): pack/3's test file silenced a Pyrefly override error with a
`pyrefly: ignore` comment, which `tests/test_strict_types.py` forbids in every source and test
file; the tripwire tuple now declares both `__getitem__` overloads instead. No requirement changes.

## Promise

Recording a formal certificate no longer builds each explored relation's encoded bytes to hash
them: the relation digest is computed from the same bytes, streamed. The explorer keeps one string
object per distinct state, and the verification layer does not hash a relation object again while
it is among the last 8 relation objects it hashed, so repeated certificate builds of the same run
hash each relation once. Every state count, transition count, invariant verdict, witness
verdict, relation digest and certificate digest is unchanged.

## Common rules for every scenario

- "The legacy digest" of a relation is
  `digest_of(ArtifactKind.FORMAL_RELATION, encode_formal_relation(relation))`, today's definition.
- A synthetic relation is a `Relation` built directly, with empty `representatives`.
- `tests/test_formal_memory.py` pins the four relation digests in the formal certificate recorded in
  mk2's own store (`formal-checker-v9`): core-v1 `6f6ff010…beb60`, effects-v1 `af0736c6…962df`,
  history-v1 `eff8e3a3…29c39`, tier-v1 `5822a72b…d422e` (full values in the test file).
- The tests call `vouch_mk2.verify.formal_relation_digest` through the module, so each test fails
  on its own before the name exists.

## Requirements

- **R1 — The streamed digest equals the legacy digest.**
  `test_streamed_relation_digest_matches_the_encoded_relation`.
  `formal_relation_digest(relation)` equals the legacy digest for the explored tier-v1 and
  effects-v1 relations, a synthetic relation whose state strings contain quotes, backslashes,
  control characters, DEL, non-ASCII text, an astral character, U+2028/U+2029 and the empty
  string, a synthetic relation with no transitions, and a one-state relation.
- **R2 — It does not build the encoding.**
  `test_streamed_relation_digest_does_not_build_the_encoded_relation`. A synthetic relation of
  4,000 states of about 812 characters and 80,000 distinct transitions (about 130 MB encoded):
  `tracemalloc`'s peak while `formal_relation_digest` runs stays under 8 MiB. (Today's encoding
  peaks at about 411 MB on the same relation.)
- **R3 — A certificate hashes relations without encoding them.**
  `test_certificate_hashes_relations_without_encoding_them`. With
  `vouch_mk2.store.codec.encode_formal_relation` (and any `vouch_mk2.verify` binding of it) replaced
  by a function that raises, `build_certificate` on a run of the tier-v1 and effects-v1 relations
  succeeds, and each certificate row's digest is the legacy digest.
- **R4 — A relation already hashed is not read again.**
  `test_a_relation_already_hashed_is_not_read_again`. The explored tier-v1 relation is rebuilt with
  `states` and `transitions` as tuples that raise when iterated or indexed once armed. After one
  `formal_relation_digest` call (equal to the recorded tier-v1 digest) both are armed; a second call
  returns the same digest, and `build_certificate` on a run of that relation puts it in the row with
  the run's counts, without reading either tuple.
- **R5 — Distinct relations keep their own digests.**
  `test_distinct_relations_keep_their_own_digests`. Alternating calls on the tier-v1 and effects-v1
  relations each return that relation's legacy digest; so does each of 30 synthetic relations of
  different content, each dropped (and garbage-collected) before the next is built so a new
  relation can reuse an earlier one's object id. The 30 digests are distinct. A relation whose
  `states` tuple records when it is freed is hashed and dropped; after 8 newer relations are
  hashed and garbage is collected, it has been freed (the memo is bounded).
- **R6 — One string object per state.** `test_explored_transitions_share_each_state_string`.
  For `explore` of tier-v1, effects-v1 and history-v1: the states are distinct, and `initial`,
  every transition's source and target, and every representative's key are the very object
  (`is`) of the equal string in `states`.
- **R7 — The formal run certifies the recorded digests.** `# guard:`
  `test_the_formal_run_certifies_the_recorded_relation_digests` (passes before the change too, at
  about 8 GB). `build_certificate(run_formal())`
  puts the four recorded relation digests in its rows, and the digest of its encoded certificate
  equals the recorded certificate's digest (`7b3919fd…689c`).

## Ground rules

- `formal_relation_digest(relation: Relation) -> str` lives in `vouch_mk2.verify`. It feeds the
  domain framing (`b"vouch-mk2/" + kind + NUL`) and then the canonical JSON of the relation document
  to SHA-256 in pieces: the same bytes `encode_formal_relation` produces, in the same order, with
  the same escaping, separators and trailing newline. It never holds the whole encoding.
  `encode_formal_relation` and `digest_of` stay as they are.
- `formal_relation_digest` remembers the digests of the last few relation objects it hashed (at
  most 8), keyed by object identity and holding a reference to each, so a remembered entry can never
  be served to a different object that reuses its id. Entries beyond the bound are dropped.
- Supported inputs: a relation's `states` and `transitions` yield the same content every time they
  are read, as every relation `explore` builds does. A tuple subclass whose iteration changes its
  content, or calls back into `formal_relation_digest`, is outside what the memo supports.
  `formal_relation_digest` does not iterate a relation while holding a lock.
- `build_certificate` takes each row's digest from `formal_relation_digest`. It never calls
  `encode_formal_relation`.
- The formal layer stays pure: `UniverseRun`, `FormalRun` and `run_formal` are unchanged, and both
  import contracts in `pyproject.toml` are unchanged (no new `ignore_imports`).
- `explore` keeps one string per distinct state: a transition's target reuses the string already
  keyed in its state map. Fingerprints stay strings with today's exact content; no hashes, integer
  ids or other encodings replace them anywhere a `Relation` exposes them.
- Unchanged: every universe's state and transition counts, invariant verdicts and details,
  witness verdicts, `CHECKER_VERSION`, the emitted tables, the relation and certificate digests,
  and every store, state and event format. Formal sizes are unchanged.
- `tests/test_formal.py::test_formal_artifact_encodings_are_stable_and_certificate_is_strict`
  (in no pack inventory) builds two encoded relations of every universe at once, about 5 GB for
  core-v1. Change only its encoding loop: compare `encode_formal_relation` twice for tier-v1 and
  effects-v1 only, and for every universe assert that the certificate row's digest equals
  `formal_relation_digest(universe.relation)`. Every other assertion stays.
- No other test file changes.

## Out of scope (a later unit)

The witness harness's re-sorts (an insertion with `bisect` instead of re-sorting 1.38 million
transitions), a `formal` pytest marker so the non-formal suite can run alone, splitting CI, and the
walkthrough test's two subprocess explorations. Not to be done at all: compacting `Snapshot`
representatives, releasing or reshaping the `run_formal` cache, parallel exploration, symmetry or
partial-order reduction, and an on-disk exploration cache (each changes counts, frozen shapes or
the certificate's claim that a run explored afresh).

## Verification outside pytest (final pass)

The full suite runs on the boat.dev default sandbox (4 vCPU, 8 GB, cgroup limit 7.25 GiB) with
swap off. It must pass with the cgroup's `memory.peak` under 4 GiB. The planner records the peak
and wall time beside the baseline: the unchanged suite was OOM-killed there without swap and took
15 to 30 minutes with 12 GB of swap.
