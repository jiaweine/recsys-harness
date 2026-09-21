import threading
import time

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



def test_conversation_detail_is_one_cross_worker_sqlite_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "conversation-detail-cross-worker.db"
    reader_store = WorkspaceStore(path)
    writer_store = WorkspaceStore(path)
    conversation = reader_store.create_conversation("snapshot", "audit")
    reader_store.add_message(
        conversation["id"],
        "assistant",
        "before",
        {"version": "before"},
    )
    before = reader_store.get_conversation(conversation["id"])

    between_reads = threading.Event()
    writer_attempted = threading.Event()
    writer_finished = threading.Event()
    reader_result: list[dict] = []
    reader_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []

    original_connect = reader_store._connect

    class InterceptConnection:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self._inner.__exit__(exc_type, exc, tb)

        def execute(self, sql, parameters=()):
            normalized = " ".join(str(sql).lower().split())
            if normalized.startswith("select * from messages"):
                between_reads.set()
                if not writer_attempted.wait(1.0):
                    raise AssertionError("writer did not attempt the cross-worker update")
                # Without an explicit read transaction the other store can commit
                # here, causing the second SELECT to observe a newer database
                # version than the already-read conversation row. With the fixed
                # boundary, commit waits until this reader releases its snapshot.
                writer_finished.wait(0.2)
            return self._inner.execute(sql, parameters)

        def rollback(self):
            return self._inner.rollback()

        def commit(self):
            return self._inner.commit()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def intercepted_connect():
        return InterceptConnection(original_connect())

    monkeypatch.setattr(reader_store, "_connect", intercepted_connect)

    def read_detail() -> None:
        try:
            reader_result.append(reader_store.get_conversation(conversation["id"]))
        except BaseException as exc:  # pragma: no cover - surfaced after join
            reader_errors.append(exc)

    def write_detail() -> None:
        try:
            if not between_reads.wait(1.0):
                raise AssertionError("reader did not reach the inter-read boundary")
            writer_attempted.set()
            writer_store.add_message(
                conversation["id"],
                "user",
                "after",
                {"version": "after"},
            )
        except BaseException as exc:  # pragma: no cover - surfaced after join
            writer_errors.append(exc)
        finally:
            writer_finished.set()

    writer = threading.Thread(target=write_detail)
    reader = threading.Thread(target=read_detail)
    writer.start()
    reader.start()
    reader.join(3.0)
    writer.join(3.0)

    assert not reader.is_alive()
    assert not writer.is_alive()
    assert not reader_errors
    assert not writer_errors
    assert len(reader_result) == 1

    snapshot = reader_result[0]
    assert snapshot["updated_at"] == before["updated_at"]
    assert [message["content"] for message in snapshot["messages"]] == ["before"]

    final = writer_store.get_conversation(conversation["id"])
    assert final["updated_at"] >= before["updated_at"]
    assert [message["content"] for message in final["messages"]] == ["before", "after"]
