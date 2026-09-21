from __future__ import annotations

import json
from pathlib import Path

import lingjing_harness.api as api_module
from lingjing_harness.domain import Catalog
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


def test_workspace_sync_revalidates_when_active_catalog_file_drifts(monkeypatch, tmp_path):
    store, _pending_file = _install_workspace(monkeypatch, tmp_path)

    # First sync establishes the current active-file stat as trusted.
    assert api_module._sync_workspace() is True

    current = build_sample_catalog()
    payload = current.to_payload()
    payload["items"][0]["title"] = "out-of-band catalog drift"
    drifted = Catalog.from_payload(payload, name=current.name)
    api_module.CATALOG_FILE.write_text(
        json.dumps(
            {"name": drifted.name, "data": drifted.to_payload()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Durable revision did not move. The stat change must force full parsing and
    # reject the uncommitted active file instead of trusting the steady fast path.
    assert store.workspace_revision() == api_module.CATALOG_REVISION
    assert api_module._sync_workspace() is False


def test_readiness_snapshot_repairs_future_clock_only_on_skew(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace-readiness-snapshot-clock.db")
    assert store.ensure_workspace_revision("rev-a") == "rev-a"
    assert store.begin_workspace_update("writer-a", lease_seconds=30, now=100.0)

    with store._lock, store._connect() as connection:  # noqa: SLF001 - skew fixture
        connection.execute(
            """
            update workspace_state
            set updated_at=?,update_until=?
            where id=1
            """,
            (1000.0, 1030.0),
        )
        connection.commit()

    state = store.workspace_readiness_snapshot("rev-a", now=200.0)
    assert state["catalog_revision"] == "rev-a"
    assert state["update_active"] is True

    with store._connect() as connection:
        row = connection.execute(
            "select updated_at,update_until from workspace_state where id=1"
        ).fetchone()
    assert float(row["updated_at"]) == 200.0
    assert float(row["update_until"]) == 230.0
