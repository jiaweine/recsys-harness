from __future__ import annotations

from types import SimpleNamespace

import lingjing_harness.api_core as api_core


def test_catalog_summary_cache_is_revision_scoped_and_copy_safe(monkeypatch):
    calls = 0

    def summary():
        nonlocal calls
        calls += 1
        return {"name": "workspace", "production_events": calls}

    fake_catalog = SimpleNamespace(summary=summary)
    monkeypatch.setattr(api_core, "catalog", fake_catalog)
    monkeypatch.setattr(api_core, "CATALOG_REVISION", "rev-a")
    monkeypatch.setattr(api_core, "_CATALOG_SUMMARY_CACHE", None)

    first = api_core._catalog_summary()
    second = api_core._catalog_summary()

    assert calls == 1
    assert first == second == {"name": "workspace", "production_events": 1}

    first["name"] = "caller-mutated"
    assert api_core._catalog_summary()["name"] == "workspace"
    assert calls == 1

    monkeypatch.setattr(api_core, "CATALOG_REVISION", "rev-b")
    refreshed = api_core._catalog_summary()

    assert calls == 2
    assert refreshed == {"name": "workspace", "production_events": 2}
