"""Tests for the no-op guard.

Both directions matter. The guard's first version only ever fired -- it read the
counts off the `<testsuites>` root, where pytest does not put them, so every run
looked like zero tests and a healthy run would have failed. It was "verified"
against a typo'd marker, which genuinely has zero tests, so the bug hid behind a
true positive. A guard that always fires is as useless as one that never does.
"""

from pathlib import Path

import pytest

from tests.live.check_executed import count, main


def _report(tmp_path: Path, body: str) -> Path:
    report = tmp_path / "report.xml"
    report.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">{body}</testsuites>')
    return report


def test_counts_come_from_inner_testsuite(tmp_path):
    """The counts live on <testsuite>; the <testsuites> root has no attributes."""
    report = _report(tmp_path, '<testsuite name="pytest" tests="52" skipped="3"></testsuite>')
    assert count(report) == (52, 3)


def test_healthy_run_passes(tmp_path):
    report = _report(tmp_path, '<testsuite name="pytest" tests="22" skipped="0"></testsuite>')
    assert main(["prog", str(report)]) == 0


def test_run_with_some_skips_passes(tmp_path):
    """A partial upstream outage is not a no-op: real checks still ran."""
    report = _report(tmp_path, '<testsuite name="pytest" tests="22" skipped="9"></testsuite>')
    assert main(["prog", str(report)]) == 0


def test_zero_collected_fails(tmp_path):
    """A typo'd marker expression deselects everything and exits 0."""
    report = _report(tmp_path, '<testsuite name="pytest" tests="0" skipped="0"></testsuite>')
    assert main(["prog", str(report)]) == 1


def test_all_skipped_fails(tmp_path):
    """A total upstream outage must not report green."""
    report = _report(tmp_path, '<testsuite name="pytest" tests="22" skipped="22"></testsuite>')
    assert main(["prog", str(report)]) == 1


def test_empty_report_fails(tmp_path):
    """No <testsuite> at all means nothing ran."""
    assert main(["prog", str(_report(tmp_path, ""))]) == 1


def test_missing_report_defers_to_pytest(tmp_path):
    """pytest died before writing a report; its own step already failed the job."""
    assert main(["prog", str(tmp_path / "absent.xml")]) == 0


@pytest.mark.parametrize("tests,skipped,expected", [(1, 0, 0), (1, 1, 1), (2, 1, 0)])
def test_boundaries(tmp_path, tests, skipped, expected):
    report = _report(tmp_path, f'<testsuite name="pytest" tests="{tests}" skipped="{skipped}"></testsuite>')
    assert main(["prog", str(report)]) == expected
