"""Frozen acceptance pack for self-hosted unit merge-concurrent-settings@1 (D-45).

Found by Astra's randomized review of nested-merge-boundary@1 (``vouch_mk2-oa2``): two branches
that each changed the same store-wide setting or open-in-CI exception merged both ways without
refusal, because replay applies the incoming change after one's own, and ended with opposite
values that later merges preserve. D-45 (the user's decision) refuses such a merge with
``merge.conflict`` when both sides changed the same key since their common history and the
values differ, and refuses a replay that would undo a value both sides already agree on. Every
test here failed against the pre-change tree, except tests marked ``# guard:``; the spec states
each test's expected failure reason (D-44). The implementer may not edit this file.
"""

from __future__ import annotations

import base64
import io
import json
import random
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from vouch_mk2.cli import main
from vouch_mk2.core import commands
from vouch_mk2.core.decide import Accepted, Refused, decide
from vouch_mk2.core.model import AnchorMode, CiPolicy, Snapshot, Tier
from vouch_mk2.store import Store
from vouch_mk2.store.digests import ArtifactKind, digest_of
from vouch_mk2.store.event_log import commit_events
from vouch_mk2.store.merge import MergeOutcome, merge_store_roots

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "merge-concurrent-settings"
SPLIT_FIXTURE = FIXTURES / "split-after-shared-write.json"
# Two real certificates and their tables, copied from mk2's own store: a merge carries a recorded
# certificate's objects, as `formal --record` commits them.
CERT_PAYLOADS = {label: (FIXTURES / f"certificate-{label}.json").read_bytes() for label in "ab"}
TABLES_PAYLOADS = {label: (FIXTURES / f"tables-{label}.json").read_bytes() for label in "ab"}
CERT_A = digest_of(ArtifactKind.FORMAL_CERTIFICATE, CERT_PAYLOADS["a"])
CERT_B = digest_of(ArtifactKind.FORMAL_CERTIFICATE, CERT_PAYLOADS["b"])
_CI_RUN_ID_VARIABLES = ("GITHUB_RUN_ID", "CI_PIPELINE_ID", "BUILDKITE_BUILD_ID", "CIRCLE_WORKFLOW_ID")

type Settings = tuple[str, str, str, str, tuple[tuple[str, str], ...]]
type Side = tuple[str, str]
"""One side of a refusal: the command verb that set the key there, and its resulting value."""


@pytest.fixture(autouse=True)
def _no_ci_run_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Common rule: no CI run-id variable is set."""
    for name in _CI_RUN_ID_VARIABLES:
        monkeypatch.delenv(name, raising=False)


# ---- helpers -----------------------------------------------------------------------------------


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
    return code, out.getvalue() + err.getvalue()


def _store(project: Path) -> str:
    return str(project / ".vouch")


def _cmd(project: Path, verb: str, *argv: str) -> str:
    """A CLI command recorded under the project's own actor, so each side's records differ."""
    code, output = _run(verb, "--root", _store(project), "--actor", project.name, *argv)
    assert code == 0, (project.name, verb, argv, output)
    return output


def _merge(ours: Path, theirs: Path) -> tuple[int, str]:
    return _run(
        "merge", "--root", _store(ours), "--other", _store(theirs), "--actor", ours.name
    )


def _snapshot(project: Path) -> Snapshot:
    with Store.open(_store(project), read_only=True) as store:
        return store.read_snapshot()


def _settings(project: Path) -> Settings:
    """The values D-45 governs, as literals."""
    snapshot = _snapshot(project)
    exceptions = tuple(sorted((item.unit_name, item.reason) for item in snapshot.ci_exceptions))
    return (
        snapshot.ci_policy.value,
        snapshot.anchor_mode.value,
        snapshot.tier.value,
        snapshot.formal_certificate_digest,
        exceptions,
    )


def _files(directory: Path) -> dict[str, bytes]:
    """Every file under a directory, by relative path, including a lock file if one exists."""
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _base(tmp_path: Path) -> Path:
    """Shared history: x@1 frozen label-only and marked open in CI with reason ``shared``."""
    base = tmp_path / "base"
    base.mkdir()
    Store.init(_store(base)).close()
    _cmd(base, "specify", "--name", "x", "--version", "1")
    _cmd(base, "freeze", "--name", "x", "--pack-version", "x-pack/1")
    _cmd(base, "mark-open-in-ci", "--name", "x", "--reason", "shared")
    return base


def _fork(base: Path, *names: str) -> tuple[Path, ...]:
    forks: list[Path] = []
    for name in names:
        target = base.parent / name
        shutil.copytree(base, target)
        forks.append(target)
    return tuple(forks)


def _two_sides(
    tmp_path: Path,
    ours_events: Callable[[Path], object],
    theirs_events: Callable[[Path], object],
    base: Path | None = None,
) -> tuple[Path, Path]:
    ours, theirs = _fork(_base(tmp_path) if base is None else base, "ours", "theirs")
    ours_events(ours)
    theirs_events(theirs)
    return ours, theirs


def _refusal_detail(target: Path, source: Path) -> str:
    """Merge SOURCE into TARGET with --json; require one merge.conflict and return its detail."""
    code, output = _run(
        "merge", "--root", _store(target), "--other", _store(source), "--actor", target.name,
        "--json",
    )
    assert code == 2, (target.name, output)
    document = json.loads(output)
    assert document["accepted"] is False, document
    violations = document["violations"]
    assert len(violations) == 1, violations
    assert violations[0]["code"] == "merge.conflict", violations
    detail = violations[0]["detail"]
    assert isinstance(detail, str)
    return detail


def _sides(detail: str) -> tuple[str, str]:
    """The OURS part (from ``OURS`` up to ``THEIRS``) and the THEIRS part (from ``THEIRS`` on)."""
    assert detail.count("OURS") == 1 and detail.count("THEIRS") == 1, detail
    start, middle = detail.index("OURS"), detail.index("THEIRS")
    assert start < middle, detail
    return detail[start:middle], detail[middle:]


def _assert_refused(target: Path, source: Path, key: str, mine: Side, other: Side) -> None:
    """TARGET<-SOURCE refuses naming KEY, TARGET's side in the OURS part and SOURCE's in THEIRS,
    and no file of either store changes."""
    before = (_files(target), _files(source))
    detail = _refusal_detail(target, source)
    assert key in detail, (key, detail)
    ours_part, theirs_part = _sides(detail)
    for needle in mine:
        assert needle in ours_part, (needle, ours_part)
    for needle in other:
        assert needle in theirs_part, (needle, theirs_part)
    assert (_files(target), _files(source)) == before, target.name


def _assert_refused_both_ways(
    ours: Path, theirs: Path, key: str, ours_side: Side, theirs_side: Side
) -> None:
    _assert_refused(ours, theirs, key, ours_side, theirs_side)
    _assert_refused(theirs, ours, key, theirs_side, ours_side)


def _assert_exchange_agrees(ours: Path, theirs: Path) -> Settings:
    """Both directions succeed from untouched copies and end with equal settings."""
    (ours_copy,) = _fork(ours, f"{ours.name}-copy")
    (theirs_copy,) = _fork(theirs, f"{theirs.name}-copy")
    code, output = _merge(ours_copy, theirs)
    assert code == 0, output
    code, output = _merge(theirs_copy, ours)
    assert code == 0, output
    assert _settings(ours_copy) == _settings(theirs_copy)
    return _settings(ours_copy)


def _apply(project: Path, command: commands.Command) -> bool:
    """Decide and commit one library command under the project's actor; False if refused.

    A certificate record is committed with its certificate and tables objects, as
    `formal --record` commits them.
    """
    objects: tuple[tuple[ArtifactKind, bytes], ...] = ()
    if isinstance(command, commands.RecordFormalCertificate):
        label = next(
            label
            for label, data in CERT_PAYLOADS.items()
            if digest_of(ArtifactKind.FORMAL_CERTIFICATE, data) == command.certificate_digest
        )
        objects = (
            (ArtifactKind.FORMAL_CERTIFICATE, CERT_PAYLOADS[label]),
            (ArtifactKind.FORMAL_TABLES, TABLES_PAYLOADS[label]),
        )
    with Store.open(_store(project)) as store:
        snapshot = store.read_snapshot()
        decision = decide(snapshot, command)
        if isinstance(decision, Refused):
            return False
        assert isinstance(decision, Accepted)
        commit_events(
            store, snapshot, decision.events, actor=project.name, run_id="", objects=objects
        )
    return True


def _must_apply(project: Path, command: commands.Command) -> None:
    assert _apply(project, command), (project.name, command)


# ---- R1-R4: concurrent store-wide settings with different values ------------------------------


def test_concurrent_ci_policies_are_refused(tmp_path: Path) -> None:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "set-ci-policy", "--policy", "strict"),
        lambda p: _cmd(p, "set-ci-policy", "--policy", "ledger"),
    )
    _assert_refused_both_ways(
        ours, theirs, "ci", ("set-ci-policy", "strict"), ("set-ci-policy", "ledger")
    )


def test_concurrent_anchor_modes_are_refused(tmp_path: Path) -> None:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "set-anchor-mode", "--mode", "ci"),
        lambda p: _cmd(p, "set-anchor-mode", "--mode", "none"),
    )
    _assert_refused_both_ways(
        ours, theirs, "anchor", ("set-anchor-mode", "ci"), ("set-anchor-mode", "none")
    )


def test_concurrent_tier_promotions_are_refused(tmp_path: Path) -> None:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "set-tier", "--tier", "standard"),
        lambda p: _cmd(p, "set-tier", "--tier", "high"),
    )
    _assert_refused_both_ways(
        ours, theirs, "tier", ("set-tier", "standard"), ("set-tier", "high")
    )


def test_concurrent_formal_certificates_are_refused(tmp_path: Path) -> None:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _must_apply(p, commands.RecordFormalCertificate(CERT_A)),
        lambda p: _must_apply(p, commands.RecordFormalCertificate(CERT_B)),
    )
    _assert_refused_both_ways(
        ours,
        theirs,
        "certificate",
        ("record-formal-certificate", CERT_A),
        ("record-formal-certificate", CERT_B),
    )


# ---- R5-R7: concurrent open-in-CI exception changes with different values ----------------------


def _clear(project: Path) -> None:
    _cmd(project, "clear-open-in-ci", "--name", "x")


def _clear_and_remark(project: Path) -> None:
    _clear(project)
    _cmd(project, "mark-open-in-ci", "--name", "x", "--reason", "shared")


def test_a_clear_against_a_clear_and_remark_is_refused(tmp_path: Path) -> None:
    ours, theirs = _two_sides(tmp_path, _clear, _clear_and_remark)
    _assert_refused_both_ways(
        ours, theirs, "x@1", ("clear-open-in-ci", "absent"), ("mark-open-in-ci", "shared")
    )


def test_marks_with_different_reasons_are_refused(tmp_path: Path) -> None:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "mark-open-in-ci", "--name", "x", "--reason", "wait for CI"),
        lambda p: _cmd(p, "mark-open-in-ci", "--name", "x", "--reason", "Wait for CI"),
    )
    _assert_refused_both_ways(
        ours, theirs, "x@1", ("mark-open-in-ci", "wait for CI"), ("mark-open-in-ci", "Wait for CI")
    )


def _with_done_unit(tmp_path: Path) -> Path:
    """The common base plus y@1 taken to done-pending-acceptance and marked open in CI."""
    base = _base(tmp_path)
    (base / "t.py").write_text("assert True\n")
    _cmd(base, "specify", "--name", "y", "--version", "1")
    _cmd(
        base, "freeze", "--name", "y", "--pack-version", "y-pack/1", "--project", str(base),
        "--pack-test-file", "t.py",
    )
    _cmd(base, "mark-ready", "--name", "y")
    _cmd(base, "start", "--name", "y")
    for kind, status in (("red", 1), ("green", 0)):
        _cmd(
            base, "record-evidence", "--unit", "y@1", "--kind", kind, "--",
            sys.executable, "-c", f"raise SystemExit({status})",
        )
    _cmd(base, "mark-done", "--name", "y")
    _cmd(base, "mark-open-in-ci", "--name", "y", "--reason", "y shared")
    return base


def test_a_lifecycle_removal_against_a_remark_is_refused(tmp_path: Path) -> None:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "publish", "--name", "y"),
        lambda p: _cmd(p, "mark-open-in-ci", "--name", "y", "--reason", "theirs reason"),
        base=_with_done_unit(tmp_path),
    )
    _assert_refused_both_ways(
        ours, theirs, "y@1", ("publish", "absent"), ("mark-open-in-ci", "theirs reason")
    )


# ---- R8-R9: values that reached a side through an earlier merge --------------------------------


def _materialize(tmp_path: Path, label: str) -> Path:
    document = json.loads(SPLIT_FIXTURE.read_text())
    project = tmp_path / label
    for relative, encoded in document["stores"][label].items():
        target = project / ".vouch" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded))
    return project


def test_a_replay_that_would_undo_an_agreed_value_is_refused(tmp_path: Path) -> None:
    ours, theirs = _materialize(tmp_path, "ours"), _materialize(tmp_path, "theirs")
    assert _settings(ours)[0] == _settings(theirs)[0] == "strict"
    (theirs_copy,) = _fork(theirs, "theirs-copy")
    _assert_refused(ours, theirs, "ci", ("set-ci-policy", "strict"), ("set-ci-policy", "ledger"))
    code, output = _merge(theirs_copy, ours)
    assert code == 0, output
    assert _settings(theirs_copy)[0] == "strict"


def test_a_value_carried_from_a_third_store_conflicts(tmp_path: Path) -> None:
    a, b, c = _fork(_base(tmp_path), "a", "b", "c")
    _cmd(a, "specify", "--name", "a-only", "--version", "1")
    _cmd(c, "set-ci-policy", "--policy", "ledger")
    code, output = _merge(a, c)
    assert code == 0, output
    _cmd(b, "set-ci-policy", "--policy", "strict")
    _assert_refused_both_ways(a, b, "ci", ("set-ci-policy", "ledger"), ("set-ci-policy", "strict"))


# ---- R10: Git-stage recovery refuses and leaves the index --------------------------------------


def _git(project: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(project), *argv),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _must_git(project: Path, *argv: str) -> str:
    result = _git(project, *argv)
    assert result.returncode == 0, (argv, result.stdout)
    return result.stdout


def _commit_store(project: Path, message: str) -> None:
    code, output = _run("generate", "--root", _store(project))
    assert code == 0, output
    _must_git(project, "add", "--", ".vouch")
    _must_git(project, "commit", "-m", message)


def test_git_stage_recovery_refuses_concurrent_policies_and_keeps_the_index(
    tmp_path: Path,
) -> None:
    project = _base(tmp_path)
    _must_git(project, "init", "-b", "main")
    _must_git(project, "config", "user.name", "Vouch Test")
    _must_git(project, "config", "user.email", "vouch@example.invalid")
    (project / ".gitignore").write_text(".vouch/.lock\n")
    _must_git(project, "add", "--", ".gitignore")
    _commit_store(project, "shared")
    _must_git(project, "checkout", "-b", "incoming")
    _cmd(project, "set-ci-policy", "--policy", "ledger")
    _commit_store(project, "incoming")
    _must_git(project, "checkout", "main")
    _cmd(project, "set-ci-policy", "--policy", "strict")
    _commit_store(project, "current")
    assert _git(project, "merge", "--no-edit", "incoming").returncode != 0
    index = (project / ".git" / "index").read_bytes()
    stages = _must_git(project, "ls-files", "--stage")
    store = _files(project / ".vouch")
    code, output = _run("merge-git-stages", "--root", _store(project))
    assert code == 2, output
    assert "merge.conflict" in output, output
    ours_part, theirs_part = _sides(output)
    assert "set-ci-policy" in ours_part and "strict" in ours_part, ours_part
    assert "set-ci-policy" in theirs_part and "ledger" in theirs_part, theirs_part
    assert (project / ".git" / "index").read_bytes() == index
    assert _must_git(project, "ls-files", "--stage") == stages
    assert _files(project / ".vouch") == store


# ---- R11: a refusal, then alignment, then a merge ----------------------------------------------


def test_aligning_after_a_refusal_then_retrying_merges(tmp_path: Path) -> None:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "set-ci-policy", "--policy", "strict"),
        lambda p: _cmd(p, "set-ci-policy", "--policy", "ledger"),
    )
    _assert_refused(ours, theirs, "ci", ("set-ci-policy", "strict"), ("set-ci-policy", "ledger"))
    _cmd(theirs, "set-ci-policy", "--policy", "strict")
    assert _assert_exchange_agrees(ours, theirs)[0] == "strict"


# ---- R12: two-way agreement over random legal histories ----------------------------------------


_POLICIES = (CiPolicy.STRICT, CiPolicy.LEDGER)
_MODES = (AnchorMode.NONE, AnchorMode.CI)
_TIERS = (Tier.STANDARD, Tier.HIGH)
_REASONS = ("r1", "r2")
_CERTS = (CERT_A, CERT_B)


def _random_step(rng: random.Random, stores: tuple[Path, ...], step: int) -> None:
    project = rng.choice(stores)
    roll = rng.randrange(9)
    if roll == 0:
        _apply(project, commands.SetCiPolicy(rng.choice(_POLICIES)))
    elif roll == 1:
        _apply(project, commands.SetAnchorMode(rng.choice(_MODES)))
    elif roll == 2:
        _apply(project, commands.SetTier(rng.choice(_TIERS)))
    elif roll == 3:
        _apply(project, commands.RecordFormalCertificate(rng.choice(_CERTS)))
    elif roll == 4:
        _apply(project, commands.MarkOpenInCi("x", rng.choice(_REASONS)))
    elif roll == 5:
        _apply(project, commands.ClearOpenInCi("x"))
    elif roll == 6:
        _apply(project, commands.SpecifyUnit(f"{project.name}-{step}", "1"))
    else:
        source = rng.choice([store for store in stores if store != project])
        merge_store_roots(_store(project), _store(source), actor=project.name, run_id="")


def test_any_two_way_exchange_that_succeeds_agrees(tmp_path: Path) -> None:
    agreed = 0
    disagreements: list[tuple[int, Settings, Settings]] = []
    for seed in range(40):
        rng = random.Random(seed)
        root = tmp_path / f"seed{seed}"
        root.mkdir()
        stores = _fork(_base(root), "a", "b", "c")
        for step in range(rng.randrange(3, 9)):
            _random_step(rng, stores, step)
        a, b = stores[0], stores[1]
        (a_copy,) = _fork(a, "a-copy")
        (b_copy,) = _fork(b, "b-copy")
        into_a = merge_store_roots(_store(a_copy), _store(b), actor="a", run_id="")
        into_b = merge_store_roots(_store(b_copy), _store(a), actor="b", run_id="")
        if isinstance(into_a, MergeOutcome) and isinstance(into_b, MergeOutcome):
            agreed += 1
            if _settings(a_copy) != _settings(b_copy):
                disagreements.append((seed, _settings(a_copy), _settings(b_copy)))
    assert disagreements == [], disagreements
    assert agreed > 0


# ---- guards: what still merges -----------------------------------------------------------------


def test_both_sides_clearing_still_merges_and_agrees(tmp_path: Path) -> None:  # guard:
    ours, theirs = _two_sides(tmp_path, _clear, _clear)
    assert _assert_exchange_agrees(ours, theirs)[4] == ()


def test_same_reason_marks_still_merge(tmp_path: Path) -> None:  # guard:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "mark-open-in-ci", "--name", "x", "--reason", "same"),
        lambda p: _cmd(p, "mark-open-in-ci", "--name", "x", "--reason", "same"),
    )
    assert _assert_exchange_agrees(ours, theirs)[4] == (("x@1", "same"),)


def test_several_writes_ending_equal_still_merge(tmp_path: Path) -> None:  # guard:
    def ours_events(project: Path) -> None:
        for policy in ("ledger", "strict", "ledger"):
            _cmd(project, "set-ci-policy", "--policy", policy)

    ours, theirs = _two_sides(
        tmp_path, ours_events, lambda p: _cmd(p, "set-ci-policy", "--policy", "ledger")
    )
    assert _assert_exchange_agrees(ours, theirs)[0] == "ledger"


def test_a_one_sided_change_still_merges(tmp_path: Path) -> None:  # guard:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "specify", "--name", "ours-only", "--version", "1"),
        lambda p: _cmd(p, "set-tier", "--tier", "standard"),
    )
    assert _assert_exchange_agrees(ours, theirs)[2] == "standard"


def test_changes_to_different_keys_still_merge(tmp_path: Path) -> None:  # guard:
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "set-ci-policy", "--policy", "ledger"),
        lambda p: _cmd(p, "set-anchor-mode", "--mode", "ci"),
    )
    assert _assert_exchange_agrees(ours, theirs)[:2] == ("ledger", "ci")


def test_exceptions_on_different_units_still_merge(tmp_path: Path) -> None:  # guard:
    base = _base(tmp_path)
    _cmd(base, "specify", "--name", "z", "--version", "1")
    _cmd(base, "freeze", "--name", "z", "--pack-version", "z-pack/1")
    _cmd(base, "mark-open-in-ci", "--name", "z", "--reason", "z shared")
    ours, theirs = _two_sides(
        tmp_path,
        lambda p: _cmd(p, "mark-open-in-ci", "--name", "x", "--reason", "ours x"),
        lambda p: _cmd(p, "mark-open-in-ci", "--name", "z", "--reason", "theirs z"),
        base=base,
    )
    assert _assert_exchange_agrees(ours, theirs)[4] == (("x@1", "ours x"), ("z@1", "theirs z"))


# ---- R13: the quickstart states it -------------------------------------------------------------


def test_quickstart_states_the_concurrent_change_rule() -> None:
    text = " ".join((ROOT / "docs" / "quickstart.md").read_text().split())
    assert (
        "A merge refuses with `merge.conflict` when both sides changed the same setting or "
        "open-in-CI exception to different values" in text
    )
    assert (
        "If OURS cleared it and THEIRS cleared and marked it again, the merge refuses with "
        "`merge.conflict`" in text
    )
