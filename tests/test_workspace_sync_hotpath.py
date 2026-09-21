from __future__ import annotations

import json
from pathlib import Path

import lingjing_harness.api as api_module
from lingjing_harness.domain import Catalog
from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.store import WorkspaceStore


def _write_catalog(path: Path, catalog: Catalog) -> None:
    path.write_text(
        json.dumps(
            {"name": catalog.name, "data": catalog.to_payload()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _changed_catalog() -> Catalog:
    base = build_sample_catalog()
    payload = base.to_payload()
    payload["items"][0]["title"] = "workspace sync drift"
    return Catalog.from_payload(payload, name="workspace sync drift")


def _install_workspace(monkeypatch, tmp_path: Path, catalog: Catalog) -> WorkspaceStore:
    catalog_file = tmp_path / "catalog.json"
    pending_file = tmp_path / "catalog.pending.json"
    store = WorkspaceStore(tmp_path / "workspace.db")
    revision = api_module.catalog_fingerprint(catalog)

    monkeypatch.setattr(api_module, "CATALOG_FILE", catalog_file)
    monkeypatch.setattr(api_module, "CATALOG_PENDING_FILE", pending_file)
    monkeypatch.setattr(api_module, "catalog", catalog)
    monkeypatch.setattr(
        api_module,
        "harness",
        api_module.AgentHarness(catalog, memory=api_module.memory),
    )
    monkeypatch.setattr(api_module, "CATALOG_REVISION", revision)
    monkeypatch.setattr(api_module, "RUNS", {})
    monkeypatch.setattr(api_module, "store", store)
    _write_catalog(catalog_file, catalog)
    assert store.ensure_workspace_revision(revision) == revision

    # The transaction boundary was installed against the process's original
    # CATALOG_FILE. One full sync re-anchors its cheap file signature to this
    # isolated fixture before each fast-path assertion.
    assert api_module._sync_workspace() is True
    return store


def test_steady_workspace_sync_skips_catalog_reload_and_cleanup_writes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    catalog = build_sample_catalog()
    store = _install_workspace(monkeypatch, tmp_path, catalog)

    def forbidden_load():
        raise AssertionError("steady workspace sync must not reload the catalog")

    def forbidden_write(*args, **kwargs):
        raise AssertionError("steady workspace sync must not acquire cleanup writes")

    monkeypatch.setattr(api_module, "_load_catalog", forbidden_load)
    monkeypatch.setattr(store, "begin_workspace_update", forbidden_write)
    monkeypatch.setattr(store, "abort_workspace_update", forbidden_write)
    monkeypatch.setattr(store, "finish_workspace_publication", forbidden_write)

    assert api_module._sync_workspace() is True


def test_workspace_sync_detects_active_file_structural_drift(
    monkeypatch,
    tmp_path: Path,
) -> None:
    previous = build_sample_catalog()
    incoming = _changed_catalog()
    previous_revision = api_module.catalog_fingerprint(previous)
    store = _install_workspace(monkeypatch, tmp_path, previous)
    catalog_file = api_module.CATALOG_FILE

    _write_catalog(catalog_file, incoming)

    assert api_module._sync_workspace() is False
    assert api_module.CATALOG_REVISION == previous_revision
    assert store.workspace_revision() == previous_revision
    assert api_module.catalog_fingerprint(api_module.catalog) == previous_revision


def test_identical_active_file_rewrite_reanchors_fast_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    catalog = build_sample_catalog()
    _install_workspace(monkeypatch, tmp_path, catalog)
    catalog_file = api_module.CATALOG_FILE

    original_load = api_module._load_catalog
    loads = 0

    def counted_load():
        nonlocal loads
        loads += 1
        return original_load()

    monkeypatch.setattr(api_module, "_load_catalog", counted_load)
    _write_catalog(catalog_file, catalog)

    assert api_module._sync_workspace() is True
    assert loads == 1

    assert api_module._sync_workspace() is True
    assert loads == 1
