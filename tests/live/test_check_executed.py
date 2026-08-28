from pathlib import Path

from tests.live.check_executed import main


def _report(tmp_path: Path, tests: int, skipped: int) -> Path:
    report = tmp_path / "report.xml"
    report.write_text(f'<?xml version="1.0"?><testsuites><testsuite tests="{tests}" skipped="{skipped}"/></testsuites>')
    return report


def test_live_guard_accepts_a_run_that_executed_checks(tmp_path):
    assert main(["check", str(_report(tmp_path, tests=22, skipped=3))]) == 0


def test_live_guard_rejects_a_run_that_skipped_every_check(tmp_path):
    assert main(["check", str(_report(tmp_path, tests=22, skipped=22))]) == 1
