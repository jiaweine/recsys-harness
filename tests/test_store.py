from lingjing_harness.store import WorkspaceStore


def test_store_roundtrip(tmp_path):
    s=WorkspaceStore(tmp_path/"workspace.db")
    c=s.create_conversation()
    s.add_message(c["id"],"user","测试体验问题")
    s.add_message(c["id"],"assistant","完成",{"ok":True})
    loaded=s.get_conversation(c["id"])
    assert loaded["title"].startswith("测试体验问题")
    assert loaded["messages"][-1]["payload"]["ok"] is True


def test_two_workers_cannot_reserve_same_conversation(tmp_path):
    path = tmp_path / "leases.db"
    one = WorkspaceStore(path)
    two = WorkspaceStore(path)
    conversation = one.create_conversation()
    now = __import__('time').time()
    first = {"run_id":"run-one","conversation_id":conversation["id"],"goal":"one","status":"running","events":[],"created_at":now,"updated_at":now}
    second = {"run_id":"run-two","conversation_id":conversation["id"],"goal":"two","status":"running","events":[],"created_at":now,"updated_at":now}
    assert one.reserve_run("run-one", conversation["id"], "one", first, owner_id="worker-one", lease_seconds=10) is True
    assert two.reserve_run("run-two", conversation["id"], "two", second, owner_id="worker-two", lease_seconds=10) is False
    assert two.active_run_for_conversation(conversation["id"])["run_id"] == "run-one"


def test_run_lease_prevents_duplicate_recovery_until_expired(tmp_path):
    import time
    path = tmp_path / "lease-recovery.db"
    one = WorkspaceStore(path)
    two = WorkspaceStore(path)
    conversation = one.create_conversation()
    now = time.time()
    snapshot = {"run_id":"run-lease","conversation_id":conversation["id"],"goal":"recover","status":"running","events":[],"created_at":now,"updated_at":now}
    assert one.reserve_run("run-lease", conversation["id"], "recover", snapshot, owner_id="worker-one", lease_seconds=2)
    assert two.claim_recoverable_runs(owner_id="worker-two", lease_seconds=2, now=now + .5) == []
    claimed = two.claim_recoverable_runs(owner_id="worker-two", lease_seconds=2, now=now + 3)
    assert [row["run_id"] for row in claimed] == ["run-lease"]
    assert two.get_run("run-lease")["owner_id"] == "worker-two"


def test_remote_cancel_wins_over_stale_running_checkpoint(tmp_path):
    import time
    path = tmp_path / "remote-cancel.db"
    owner = WorkspaceStore(path)
    remote = WorkspaceStore(path)
    conversation = owner.create_conversation()
    now = time.time()
    snapshot = {"run_id":"run-cancel","conversation_id":conversation["id"],"goal":"cancel","status":"running","events":[],"created_at":now,"updated_at":now}
    assert owner.reserve_run("run-cancel", conversation["id"], "cancel", snapshot, owner_id="worker-one", lease_seconds=10)
    assert remote.request_cancel("run-cancel") == "cancel_requested"
    stale = {**snapshot, "status":"running", "updated_at":time.time()}
    persisted = owner.save_run("run-cancel", conversation["id"], "cancel", "running", stale, owner_id="worker-one", lease_seconds=10)
    assert persisted == "cancel_requested"
    assert remote.run_status("run-cancel") == "cancel_requested"
