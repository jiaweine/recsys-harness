from __future__ import annotations

from pathlib import Path

import copy
import time

import lingjing_harness.api as api_module


def _run_row(run_id: str, conversation_id: str, events: int = 6) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "delta poll",
        "status": "running",
        "events": [
            {"phase": "execute", "progress": index, "detail": f"event-{index}"}
            for index in range(events)
        ],
        "result": None,
        "attachments": [],
        "attachment_ids": [],
        "allow_network": False,
        "catalog_revision": api_module.CATALOG_REVISION,
        "checkpoint": {"cycle": events, "status": "running", "events": []},
        "created_at": now,
        "updated_at": now,
    }


def test_run_delta_poll_is_opt_in_and_preserves_full_default(tmp_path, monkeypatch):
    store = api_module.WorkspaceStore(tmp_path / "run-delta.db")
    monkeypatch.setattr(api_module, "store", store)
    conversation = store.create_conversation("delta", "audit")
    row = _run_row("job-delta", conversation["id"], events=6)
    assert store.reserve_run(
        row["run_id"],
        row["conversation_id"],
        row["goal"],
        row,
        owner_id=api_module.WORKER_ID,
        lease_seconds=30,
    )
    with api_module.RUN_LOCK:
        api_module.RUNS[row["run_id"]] = copy.deepcopy(row)

    full = api_module.get_run(row["run_id"])
    delta = api_module.get_run(row["run_id"], after_event=4)

    assert len(full["events"]) == 6
    assert "event_count" not in full
    assert [event["detail"] for event in delta["events"]] == ["event-4", "event-5"]
    assert delta["events_from"] == 4
    assert delta["event_count"] == 6
    assert "checkpoint" not in delta

    with api_module.RUN_LOCK:
        api_module.RUNS.pop(row["run_id"], None)


def test_run_delta_poll_clamps_cursor_and_keeps_terminal_payload(tmp_path, monkeypatch):
    store = api_module.WorkspaceStore(tmp_path / "run-delta-terminal.db")
    monkeypatch.setattr(api_module, "store", store)
    conversation = store.create_conversation("delta terminal", "audit")
    row = _run_row("job-delta-terminal", conversation["id"], events=3)
    row.update(
        {
            "status": "completed",
            "result": {"answer": "done", "events": copy.deepcopy(row["events"])},
            "message": {"id": "msg-final", "role": "assistant", "content": "done"},
        }
    )
    store.save_run(
        row["run_id"],
        row["conversation_id"],
        row["goal"],
        "completed",
        row,
    )

    delta = api_module.get_run(row["run_id"], after_event=99)

    assert delta["status"] == "completed"
    assert delta["events"] == []
    assert delta["events_from"] == 3
    assert delta["event_count"] == 3
    assert delta["result"]["answer"] == "done"
    assert delta["message"]["id"] == "msg-final"


def test_web_client_requests_only_unseen_run_events() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "frontend" / "app.js"
    ).read_text(encoding="utf-8")

    assert "liveEvents:[]" in source
    assert "?after_event=${cursor}" in source
    assert "state.liveEvents.push(...r.events)" in source
    assert "from!==state.liveEvents.length" in source
    assert "event_count" in source
