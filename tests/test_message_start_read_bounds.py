import asyncio

from fastapi.testclient import TestClient

import lingjing_harness.api as api_module
from lingjing_harness.api import app
from lingjing_harness.store import WorkspaceStore


def test_user_message_first_row_probe_uses_early_stop_query(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "workspace.db")
    conversation = store.create_conversation("seed", "search")

    first = store.add_message(conversation["id"], "user", "first task")
    assert first["role"] == "user"
    assert store.get_conversation(conversation["id"])["title"] == "first task"

    statements: list[str] = []
    original_connect = store._connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "_connect", traced_connect)
    second = store.add_message(conversation["id"], "user", "second task")
    assert second["role"] == "user"

    normalized = [statement.lower() for statement in statements]
    assert any(
        "select 1 from messages where conversation_id=" in statement
        and "limit 1" in statement
        for statement in normalized
    )
    assert not any("count(" in statement for statement in normalized)


def test_message_post_does_not_load_or_decode_conversation_history(monkeypatch):
    async def no_execute(*args, **kwargs):
        await asyncio.sleep(0)

    monkeypatch.setattr(api_module, "_execute", no_execute)

    run_id = None
    with TestClient(app) as client:
        conversation = client.post(
            "/api/conversations",
            json={"scene": "search", "title": "history"},
        ).json()
        conversation_id = conversation["id"]

        for index in range(128):
            api_module.store.add_message(
                conversation_id,
                "assistant",
                f"historical message {index}",
                {"index": index},
            )

        def history_scan_is_forbidden(*args, **kwargs):
            raise AssertionError("task-start POST must not load full conversation history")

        monkeypatch.setattr(api_module.store, "list_messages", history_scan_is_forbidden)
        accepted = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "start another bounded task"},
        )
        assert accepted.status_code == 200
        run_id = accepted.json()["run_id"]

    if run_id is not None:
        with api_module.RUN_LOCK:
            api_module.RUNS.pop(run_id, None)
        api_module.store.delete_run(run_id)


def test_message_start_scope_does_not_change_conversation_detail_reads(monkeypatch):
    async def no_execute(*args, **kwargs):
        await asyncio.sleep(0)

    monkeypatch.setattr(api_module, "_execute", no_execute)

    run_id = None
    with TestClient(app) as client:
        conversation = client.post(
            "/api/conversations",
            json={"scene": "audit", "title": "detail"},
        ).json()
        conversation_id = conversation["id"]
        api_module.store.add_message(
            conversation_id,
            "assistant",
            "existing history",
            {"kind": "fixture"},
        )

        accepted = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "bounded start"},
        )
        assert accepted.status_code == 200
        run_id = accepted.json()["run_id"]

        detail = client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        messages = detail.json()["messages"]
        assert [message["content"] for message in messages][-2:] == [
            "existing history",
            "bounded start",
        ]

    if run_id is not None:
        with api_module.RUN_LOCK:
            api_module.RUNS.pop(run_id, None)
        api_module.store.delete_run(run_id)
