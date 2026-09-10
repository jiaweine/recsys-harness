import threading

from lingjing_harness.store import WorkspaceStore


def test_conversation_history_read_does_not_hold_writer_mutex(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "workspace.db")
    conversation = store.create_conversation("seed", "search")
    store.add_message(conversation["id"], "user", "existing task")

    history_started = threading.Event()
    release_history = threading.Event()
    write_finished = threading.Event()
    reader_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []

    original_list_messages = store.list_messages

    def blocked_history_read(conversation_id: str):
        history_started.set()
        if not release_history.wait(2.0):
            raise AssertionError("test history read was not released")
        return original_list_messages(conversation_id)

    monkeypatch.setattr(store, "list_messages", blocked_history_read)

    def read_detail() -> None:
        try:
            detail = store.get_conversation(conversation["id"])
            assert detail["messages"][-1]["content"] == "second task"
        except BaseException as exc:  # pragma: no cover - surfaced after join
            reader_errors.append(exc)

    def write_message() -> None:
        try:
            store.add_message(conversation["id"], "user", "second task")
        except BaseException as exc:  # pragma: no cover - surfaced after join
            writer_errors.append(exc)
        finally:
            write_finished.set()

    reader = threading.Thread(target=read_detail)
    reader.start()
    assert history_started.wait(1.0)

    writer = threading.Thread(target=write_message)
    writer.start()
    assert write_finished.wait(0.5), (
        "a full conversation history read must not hold the process-local "
        "writer mutex while message writes wait"
    )

    release_history.set()
    reader.join(2.0)
    writer.join(2.0)

    assert not reader.is_alive()
    assert not writer.is_alive()
    assert not reader_errors
    assert not writer_errors
