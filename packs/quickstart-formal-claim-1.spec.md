# quickstart-formal-claim@1 acceptance pack

**Owner:** Claude (acceptance owner). **Implementer:** GPT 6.1 Sol, who may not edit this file
or `tests/test_quickstart_formal_claim.py`. **Decision:** D-28. **Beads:** `vouch_mk2-4zh.1`
(unit), `vouch_mk2-4zh.1.1` (implement).

## Problem

`docs/quickstart.md` §8 says that at `high`, `check` "reports `NOT_CHECKED` for invariant
execution and mutation-witness discrimination". The code reports one row,
`check.formal-certificate`. When a certificate is recorded, at any tier, that row is `PASS` for
the narrowed obligation, and its detail names what `check` did not perform: regenerating the
reachable relations and running M1–M11 and the witness mutations (`src/vouch_mk2/verify.py`).
`docs/claim-boundary.md` § Formal certificate already states this.

## Requirements

- **R1** `test_quickstart_names_the_formal_certificate_row_with_the_verdict_check_reports`: a
  quickstart sentence names `` `check.formal-certificate` `` and `PASS`. With a valid recorded
  certificate, the code reports that row as `PASS` at every tier, with "not performed by
  `check`" in its detail.
- **R2** `test_quickstart_attaches_no_not_checked_verdict_to_formal_exploration`: no quickstart
  prose sentence that mentions invariants, witnesses, the formal model or the exploration
  contains `NOT_CHECKED` or `NOT-CHECKED`.
- **R3** `test_quickstart_does_not_limit_certificate_verification_to_high`: a sentence naming
  `` `check.formal-certificate` `` says it applies at any (or every) tier, and no sentence
  mentioning the certificate scopes it to `high`.
- **R4** (guard) `test_quickstart_still_shows_how_to_record_the_exploration`: the quickstart
  still names `formal --record`.

## Ground rules

- Change only the prose of `docs/quickstart.md` §8. Every fenced block stays byte-identical;
  `tests/test_operator_docs.py` runs them.
- Every other test stays green, including the tests that pin phrases in the quickstart.
- Match the surrounding style: short, plain sentences. The claim boundary carries the detail,
  so the quickstart states the verdict and points there rather than repeating the full list.

## Out of scope

- Any change to `check`, the certificate, or `docs/claim-boundary.md`.
- Any other quickstart section.
