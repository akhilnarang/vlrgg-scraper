"""Fail a CI run that reported success while executing nothing.

The failure mode this guards against: a marker expression that matches no tests,
or an upstream outage skipping every check, leaves pytest exiting 0 and the job
green. `--strict-markers` does not help -- it validates markers registered on
tests, not the `-m` expression passed on the command line, so `-m "live_gold"`
silently deselects everything and exits 0.

Usage: python -m tests.live.check_executed <junit.xml>
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def count(report: Path) -> tuple[int, int]:
    """Return (tests, skipped) from a pytest JUnit XML report.

    pytest records counts on the inner ``<testsuite>`` elements; the
    ``<testsuites>`` root carries no attributes, so reading it yields zero.
    """
    suites = list(ET.parse(report).getroot().iter("testsuite"))
    return (
        sum(int(suite.get("tests", 0)) for suite in suites),
        sum(int(suite.get("skipped", 0)) for suite in suites),
    )


def main(argv: list[str]) -> int:
    report = Path(argv[1])
    if not report.exists():
        # pytest died before writing a report; that step already failed the job.
        return 0

    tests, skipped = count(report)
    if tests == 0 or tests == skipped:
        print(
            f"::error::No live check executed ({tests} collected, {skipped} skipped) "
            "-- the health job is a no-op. Check marker and collection wiring."
        )
        return 1

    print(f"{tests - skipped} of {tests} live checks executed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
