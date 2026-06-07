from concurrent.futures import Executor, Future

import pytest


class InlineExecutor(Executor):
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.shutdown()

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future


@pytest.fixture(autouse=True)
def use_inline_executor_for_rankings(monkeypatch):
    from app.services import rankings

    monkeypatch.setattr(rankings, "ProcessPoolExecutor", InlineExecutor)
