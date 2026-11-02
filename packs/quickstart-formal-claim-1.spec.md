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

- **R1** `test_quickstart_names_the_formal_certificate_row_with_the_verdict_check_reports`: the
  quickstart contains, as one paragraph (whitespace normalized): "`check` does not re-explore
  the bounded formal model. With a valid recorded certificate, `check.formal-certificate`
  reports `PASS` at every tier for the narrowed obligation in
  [claim-boundary.md](claim-boundary.md#formal-certificate). Run `python3 vouch.lock formal
  --record --root .vouch` when you intend to perform and record that expensive exploration."
  Every prose paragraph of §8, its heading included, is pinned, so §8 cannot gain or change a
  paragraph. No other prose paragraph that mentions the certificate names a verdict (pass, fail, not
  checked, not applicable), after decoding HTML entities, folding Unicode compatibility forms,
  and treating every dash and underscore as a hyphen. The verdict the pinned paragraph names is
  the verdict the code reports for the row at every tier, with "not performed by `check`" in
  its detail.
- **R2** `test_quickstart_attaches_no_not_checked_verdict_to_formal_exploration`: no quickstart
  prose sentence that mentions invariants, witnesses, the formal model or the exploration
  contains `NOT_CHECKED` or `NOT-CHECKED`.
- **R3** `test_quickstart_does_not_limit_certificate_verification_to_high`: a sentence naming
  `` `check.formal-certificate` `` says it applies at any (or every) tier, and no sentence
  mentioning the certificate scopes it to `high`.
- **R4** (guard) `test_quickstart_still_shows_how_to_record_the_exploration`: the quickstart
  still names `formal --record`.

## Amendment (pack/2, DEV-qfc-01)

The reviewer showed that pack/1's R1 only required the word `PASS` in a sentence naming the
row, so "reports `FAIL` rather than `PASS` at every tier" passed all four tests. Pack/2 pins the
implemented sentence and checks the verdict it names against the code. Against the pre-change
quickstart pack/2 fails 3/4, as pack/1 did; it rejects the inverted sentence and accepts the
implemented one.

## Amendment (pack/3, DEV-qfc-02)

The reviewer then showed that pack/2 required the pinned sentence to exist but did not stop
another sentence from naming a different verdict ("Without a recorded certificate,
`check.formal-certificate` also reports `PASS` at every tier" passed; the real verdict is
`NOT_APPLICABLE`). Pack/3 makes the pinned sentence the only quickstart sentence that mentions
the certificate and names a verdict. A further verdict statement about the certificate, true or
not, belongs in the claim boundary or needs this pack rebound.

## Amendment (pack/4, DEV-qfc-03)

The reviewer then showed two ways past pack/3's sentence rule: the splitter cut "e.g." into
two sentences, hiding a false verdict, and the verdict pattern missed other dash characters and
HTML entities ("NOT–CHECKED", "FA&#73;L"). Pack/4 pins the whole paragraph, so any insertion
into it fails, and normalizes every other paragraph before looking for a verdict.

## Amendment (pack/5, DEV-qfc-04)

The reviewer then showed that a list lead-in or a heading ("Without a recorded certificate:")
could carry the condition, and the following paragraph a false verdict ("The row reports `PASS`
at every tier."), each passing the paragraph rule on its own. Pack/5 pins every prose paragraph
of §8, where the formal verdict is stated.

## Scope

The pack guards against accidental misstatement by a maintainer (threat level 1). It checks
verdict statements only. A sentence that misdescribes what `check` or `formal` does without
naming a verdict, and text deliberately disguised to evade the check (look-alike characters,
synonyms), are outside it and left to review. Outside §8 the check works one paragraph at a
time, so a verdict separated from its certificate condition across paragraphs there is not
detected; that residual is recorded rather than chased further, because no text pattern can
decide what a passage of prose asserts.

## Ground rules

- Change only the prose of `docs/quickstart.md` §8. Every fenced block stays byte-identical;
  `tests/test_operator_docs.py` runs them.
- Every other test stays green, including the tests that pin phrases in the quickstart.
- Match the surrounding style: short, plain sentences. The claim boundary carries the detail,
  so the quickstart states the verdict and points there rather than repeating the full list.

## Out of scope

- Any change to `check`, the certificate, or `docs/claim-boundary.md`.
- Any other quickstart section.
