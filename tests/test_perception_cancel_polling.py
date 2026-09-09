from __future__ import annotations

import asyncio
import time

import lingjing_harness.api as api_module


_ROWS = [
    {
        "id": "att-000000000001",
        "name": "context.txt",
        "mime": "text/plain",
        "size": 7,
    }
]


def test_perception_wait_bounds_durable_stop_checks(monkeypatch):
    calls = 0

    def slow_context(rows, *, should_stop=None):
        deadline = time.monotonic() + 0.65
        while time.monotonic() < deadline:
            if should_stop:
                should_stop()
            time.sleep(0.02)
        return "bounded context", [{"id": rows[0]["id"]}]

    def should_stop():
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(api_module.perception, "build_context", slow_context)

    context, observed = asyncio.run(
        api_module._perceive_with_cancel(_ROWS, should_stop)
    )

    assert context == "bounded context"
    assert observed == [{"id": _ROWS[0]["id"]}]
    # One immediate check, at most one 500ms interval check, and the exact
    # completion check. The historical 100ms loop plus perception callbacks
    # would perform many more durable SQLite reads over the same interval.
    assert 2 <= calls <= 3


def test_perception_wait_still_detects_remote_cancel_within_poll_bound(monkeypatch):
    calls = 0

    def cancellable_context(rows, *, should_stop=None):
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if should_stop and should_stop():
                raise InterruptedError("cancelled")
            time.sleep(0.02)
        raise AssertionError("bounded durable cancel poll was not delivered")

    def should_stop():
        nonlocal calls
        calls += 1
        return calls >= 2

    monkeypatch.setattr(api_module.perception, "build_context", cancellable_context)
    started = time.monotonic()

    context, observed = asyncio.run(
        api_module._perceive_with_cancel(_ROWS, should_stop)
    )

    elapsed = time.monotonic() - started
    assert elapsed < 0.9
    assert context == ""
    assert observed[0]["id"] == _ROWS[0]["id"]
    assert observed[0]["perception"] == "cancelled"
    assert calls == 2
