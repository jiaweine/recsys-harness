from __future__ import annotations

import json
from pathlib import Path

import lingjing_harness.api as api_module
from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.store import WorkspaceStore


def _install_workspace(monkeypatch, tmp_path: Path):
    catalog = build_sample_catalog()
    catalog_file = tmp_path / "catalog.json"
    pending_file = tmp_path / "catalog.pending.json"
    catalog_file.write_text(
        json.dumps(
            {"name": catalog.name, "data": catalog.to_payload()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    revision = api_module.catalog_fingerprint(catalog)
    store = WorkspaceStore(tmp_path / "workspace.db")
    assert store.ensure_workspace_revision(revision) == revision

    monkeypatch.setattr(api_module, "CATALOG_FILE", catalog_file)
    monkeypatch.setattr(api_module, "CATALOG_PENDING_FILE", pending_file, raising=False)
    monkeypatch.setattr(api_module, "catalog", catalog)
    monkeypatch.setattr(
        api_module,
        "harness",
        api_module.AgentHarness(catalog, memory=api_module.memory),
    )
    monkeypatch.setattr(api_module, "CATALOG_REVISION", revision)
    monkeypatch.setattr(api_module, "store", store)
    return store, pending_file


def test_steady_workspace_sync_skips_cleanup_writer_transactions(monkeypatch, tmp_path):
    store, pending_file = _install_workspace(monkeypatch, tmp_path)
    assert not pending_file.exists()

    def unexpected_begin(*args, **kwargs):
        raise AssertionError("steady sync must not acquire a cleanup writer lease")

    def unexpected_finish(*args, **kwargs):
        raise AssertionError("steady sync must not release an absent publication fence")

    monkeypatch.setattr(store, "begin_workspace_update", unexpected_begin)
    monkeypatch.setattr(store, "finish_workspace_publication", unexpected_finish)

    assert api_module._sync_workspace() is True


def test_orphan_pending_temp_still_uses_exclusive_cleanup(monkeypatch, tmp_path):
    store, pending_file = _install_workspace(monkeypatch, tmp_path)
    pending_temp = pending_file.with_name(f"{pending_file.name}.tmp")
    pending_temp.write_text("partial", encoding="utf-8")

    original_begin = store.begin_workspace_update
    calls = 0

    def counted_begin(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_begin(*args, **kwargs)

    monkeypatch.setattr(store, "begin_workspace_update", counted_begin)

    assert api_module._sync_workspace() is True
    assert calls == 1
    assert not pending_temp.exists()
