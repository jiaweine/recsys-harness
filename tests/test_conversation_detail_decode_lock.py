import threading

from lingjing_harness.store import WorkspaceStore


def test_conversation_payload_decoding_does_not_hold_writer_mutex(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "conversation-detail-decode.db")
    conversation = store.create_conversation("reader", "audit")
    store.add_message(conversation["id"], "assistant", "visible", {"kind": "history"})

    decode_started = threading.Event()
    release_decode = threading.Event()
    write_finished = threading.Event()
    reader_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []

    original_loads = store._loads
    first_decode = True

    def blocked_loads(raw):
        nonlocal first_decode
        if first_decode:
            first_decode = False
            decode_started.set()
            if not release_decode.wait(2.0):
                raise AssertionError("test payload decode was not released")
        return original_loads(raw)

    monkeypatch.setattr(store, "_loads", blocked_loads)

    def read_detail() -> None:
        try:
            loaded = store.get_conversation(conversation["id"])
            assert loaded["messages"][-1]["payload"]["kind"] == "history"
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
    assert decode_started.wait(1.0)

    writer = threading.Thread(target=write_message)
    writer.start()
    assert write_finished.wait(1.0), (
        "conversation payload decoding must not hold the process-local writer mutex"
    )

    release_decode.set()
    reader.join(2.0)
    writer.join(2.0)

    assert not reader.is_alive()
    assert not writer.is_alive()
    assert not reader_errors
    assert not writer_errors
