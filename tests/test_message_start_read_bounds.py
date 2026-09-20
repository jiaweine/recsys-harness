import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

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



def _task_snapshot(run_id: str, conversation_id: str, goal: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": goal,
        "status": "running",
        "events": [],
        "result": None,
        "attachment_ids": [],
        "attachments": [],
        "allow_network": False,
        "catalog_revision": "test-revision",
        "created_at": now,
        "updated_at": now,
    }


def test_atomic_task_start_persists_user_message_id_in_first_snapshot(tmp_path):
    store = WorkspaceStore(tmp_path / "atomic-task-start.db")
    conversation = store.create_conversation("atomic start", "search")
    run_id = "job-atomic-start"
    goal = "检查搜索露营灯"

    status, message, persisted = store.start_run_with_user_message(
        run_id,
        conversation["id"],
        goal,
        _task_snapshot(run_id, conversation["id"], goal),
        {"attachments": [], "allow_network": False},
        owner_id="worker-a",
        lease_seconds=30,
    )

    assert status == "accepted"
    assert message is not None
    assert persisted is not None
    assert persisted["user_message_id"] == message["id"]

    durable = store.get_run(run_id)
    assert durable["user_message_id"] == message["id"]
    messages = store.list_messages(conversation["id"])
    assert [row["id"] for row in messages] == [message["id"]]
    assert messages[0]["content"] == goal


def test_atomic_task_start_workspace_conflict_writes_nothing(tmp_path):
    store = WorkspaceStore(tmp_path / "atomic-task-workspace-fence.db")
    conversation = store.create_conversation("workspace fence", "audit")
    assert store.ensure_workspace_revision("rev-a") == "rev-a"
    assert store.begin_workspace_update("writer", lease_seconds=30)

    status, message, persisted = store.start_run_with_user_message(
        "job-blocked",
        conversation["id"],
        "blocked",
        _task_snapshot("job-blocked", conversation["id"], "blocked"),
        {},
        owner_id="runner",
        lease_seconds=30,
    )

    assert status == "workspace_busy"
    assert message is None
    assert persisted is None
    assert store.list_messages(conversation["id"]) == []
    assert store.run_status("job-blocked") is None


def test_atomic_task_start_cross_store_race_has_one_message_and_one_run(tmp_path):
    path = tmp_path / "atomic-task-race.db"
    one = WorkspaceStore(path)
    two = WorkspaceStore(path)
    conversation = one.create_conversation("race", "search")
    gate = __import__("threading").Barrier(3)

    def start(store: WorkspaceStore, run_id: str):
        gate.wait(timeout=5)
        return store.start_run_with_user_message(
            run_id,
            conversation["id"],
            run_id,
            _task_snapshot(run_id, conversation["id"], run_id),
            {},
            owner_id=run_id,
            lease_seconds=30,
        )[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(start, one, "job-one")
        second = executor.submit(start, two, "job-two")
        gate.wait(timeout=5)
        statuses = [first.result(timeout=5), second.result(timeout=5)]

    assert sorted(statuses) == ["accepted", "active"]
    messages = one.list_messages(conversation["id"])
    assert len(messages) == 1
    active = one.active_run_for_conversation(conversation["id"])
    assert active is not None
    assert active["run_id"] in {"job-one", "job-two"}
    assert messages[0]["content"] == active["run_id"]


def test_api_task_start_does_not_use_legacy_three_write_path(monkeypatch):
    async def no_execute(*args, **kwargs):
        await asyncio.sleep(0)

    monkeypatch.setattr(api_module, "_execute", no_execute)

    def forbidden(*args, **kwargs):
        raise AssertionError("atomic task start must not use legacy write path")

    monkeypatch.setattr(api_module.store, "reserve_run", forbidden)
    monkeypatch.setattr(api_module.store, "add_message", forbidden)

    run_id = None
    with TestClient(app) as client:
        conversation = client.post(
            "/api/conversations",
            json={"scene": "search", "title": "atomic-api"},
        ).json()
        accepted = client.post(
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "atomic api start"},
        )
        assert accepted.status_code == 200
        run_id = accepted.json()["run_id"]

        durable = api_module.store.get_run(run_id)
        assert durable["user_message_id"] == accepted.json()["message"]["id"]
        assert run_id in api_module._PERSIST_META

    if run_id is not None:
        with api_module.RUN_LOCK:
            api_module.RUNS.pop(run_id, None)
        api_module._PERSIST_META.pop(run_id, None)
        api_module.store.delete_run(run_id)
