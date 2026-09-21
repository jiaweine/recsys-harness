from __future__ import annotations

import time

import lingjing_harness.api as api_module
from lingjing_harness.store import WorkspaceStore


def _active_snapshot(run_id: str, conversation_id: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "conversation active view",
        "status": "running",
        "events": [
            {
                "phase": "execute",
                "progress": 20,
                "payload": {"visible": True},
            }
        ],
        "result": None,
        "checkpoint": {
            "cycle": 1,
            "observations": [{"blob": "x" * 200_000}],
        },
        "created_at": now,
        "updated_at": now,
    }


def test_conversation_active_run_view_projects_only_rendered_fields(tmp_path):
    store = WorkspaceStore(tmp_path / "conversation-active-view.db")
    conversation = store.create_conversation("active view", "audit")
    snapshot = _active_snapshot("job-active-view", conversation["id"])
    assert store.reserve_run(
        snapshot["run_id"],
        conversation["id"],
        snapshot["goal"],
        snapshot,
        owner_id="worker-a",
        lease_seconds=30,
    )

    view = store.conversation_active_run_view(conversation["id"])

    assert view == {
        "run_id": snapshot["run_id"],
        "status": "running",
        "events": snapshot["events"],
    }
    assert "checkpoint" not in view
    assert "result" not in view


def test_conversation_detail_route_uses_projected_active_run_view(
    monkeypatch,
    tmp_path,
):
    store = WorkspaceStore(tmp_path / "conversation-detail-projection.db")
    conversation = store.create_conversation("detail projection", "audit")
    store.add_message(conversation["id"], "user", "hello")
    snapshot = _active_snapshot("job-detail-projection", conversation["id"])
    assert store.reserve_run(
        snapshot["run_id"],
        conversation["id"],
        snapshot["goal"],
        snapshot,
        owner_id="worker-a",
        lease_seconds=30,
    )

    monkeypatch.setattr(api_module, "store", store)

    def unexpected_full_snapshot(*args, **kwargs):
        raise AssertionError("detail route must not decode the full active run snapshot")

    monkeypatch.setattr(
        store,
        "active_run_for_conversation",
        unexpected_full_snapshot,
    )

    detail = api_module.get_conversation(conversation["id"])

    assert detail["active_run"]["run_id"] == snapshot["run_id"]
    assert detail["active_run"]["status"] == "running"
    assert detail["active_run"]["events"] == snapshot["events"]
