"""Fixtures for the live parser-health suite.

VLR outages are turned into skips by the `pytest_runtest_call` hook in the root
conftest, which applies to every `live_golden`/`live_health` test in any
directory -- no per-test opt-in to forget.
"""

import pytest


@pytest.fixture(autouse=True)
def use_inline_executor_for_rankings():
    """Undo the root conftest's inline-executor patch.

    Live checks must exercise the real `ProcessPoolExecutor` path, since a
    pickling failure there is exactly the kind of production break this suite
    exists to catch. A same-name fixture in the nearer conftest wins.
    """
    yield
