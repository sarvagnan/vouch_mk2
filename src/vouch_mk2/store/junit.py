"""Read the bounded JUnit report collected after an evidence command exits."""

from __future__ import annotations

import os
import stat
from xml.parsers import expat

from vouch_mk2.core.model import TestOutcome, TestReport
from vouch_mk2.store.codec import MAX_TEST_REPORT_CASES, valid_test_id
from vouch_mk2.store.errors import StoreError


_OUTCOME_CHILDREN = {
    "skipped": TestOutcome.SKIPPED,
    "error": TestOutcome.ERROR,
    "failure": TestOutcome.FAILED,
}
_OUTCOME_RANK = {outcome: index for index, outcome in enumerate(TestOutcome)}


def read_junit_report(path: str, *, max_bytes: int) -> TestReport:
    """Read a regular file in the supplied directory and retain only IDs and outcomes."""
    report: TestReport = {}
    depth = 0
    active_cases: list[tuple[int, str]] = []

    def start_element(name: str, attrs: dict[str, str]) -> None:
        nonlocal depth
        depth += 1
        outcome = _OUTCOME_CHILDREN.get(name)
        if outcome is not None and active_cases and depth == active_cases[-1][0] + 1:
            test_id = active_cases[-1][1]
            if _OUTCOME_RANK[outcome] > _OUTCOME_RANK[report[test_id]]:
                report[test_id] = outcome
        if name == "testcase" and depth > 1:
            if len(report) >= MAX_TEST_REPORT_CASES:
                raise StoreError(f"JUnit report exceeds {MAX_TEST_REPORT_CASES} test cases")
            test_id = attrs.get("classname", "") + "::" + attrs.get("name", "")
            if not valid_test_id(test_id):
                raise StoreError("JUnit test IDs must be 1..4096 printable characters")
            if test_id in report:
                raise StoreError(f"duplicate JUnit test ID {test_id!r}")
            report[test_id] = TestOutcome.PASSED
            active_cases.append((depth, test_id))

    def end_element(_name: str) -> None:
        nonlocal depth
        if active_cases and depth == active_cases[-1][0]:
            active_cases.pop()
        depth -= 1

    def refuse_doctype(
        _name: str, _system_id: str | None, _public_id: str | None, _internal_subset: int,
    ) -> None:
        raise StoreError("JUnit report must not contain a document type declaration")

    parser = expat.ParserCreate()
    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.StartDoctypeDeclHandler = refuse_doctype

    directory, filename = os.path.split(path)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.stat(filename, dir_fd=directory_fd, follow_symlinks=False).st_mode):
            raise StoreError("JUnit report must be a regular file")
        fd = os.open(
            filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise StoreError("JUnit report must be a regular file")
            too_large = f"JUnit report exceeds the {max_bytes}-byte artifact bound"
            if info.st_size > max_bytes:
                raise StoreError(too_large)
            remaining = max_bytes
            while chunk := os.read(fd, min(64 * 1024, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    raise StoreError(too_large)
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
        except (expat.ExpatError, LookupError, ValueError) as error:
            raise StoreError(f"invalid JUnit XML: {error}") from error
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)
    return report
