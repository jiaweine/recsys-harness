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


def test_stale_worker_cannot_commit_terminal_result_after_lease_takeover(tmp_path):
    import time
    path = tmp_path / "lease-fencing.db"
    old_worker = WorkspaceStore(path)
    new_worker = WorkspaceStore(path)
    conversation = old_worker.create_conversation()
    started = time.time()
    snapshot = {"run_id":"run-fence","conversation_id":conversation["id"],"goal":"fence","status":"running","events":[],"created_at":started,"updated_at":started}
    assert old_worker.reserve_run("run-fence", conversation["id"], "fence", snapshot, owner_id="old-worker", lease_seconds=2)
    claimed = new_worker.claim_recoverable_runs(owner_id="new-worker", lease_seconds=30, now=started + 3)
    assert [row["run_id"] for row in claimed] == ["run-fence"]
    stale_completed = {**snapshot, "status":"completed", "updated_at":started + 3.1}
    persisted = old_worker.save_run(
        "run-fence", conversation["id"], "fence", "completed", stale_completed,
        owner_id="old-worker", lease_seconds=30,
    )
    assert persisted == "running"
    current = new_worker.get_run("run-fence")
    assert current["status"] == "running"
    assert current["owner_id"] == "new-worker"


def test_workspace_update_lock_blocks_other_worker_and_new_runs(tmp_path):
    import time
    path = tmp_path / "workspace-revision.db"
    one = WorkspaceStore(path)
    two = WorkspaceStore(path)
    assert one.ensure_workspace_revision("rev-a") == "rev-a"
    assert two.workspace_revision() == "rev-a"
    assert one.begin_workspace_update("worker-one", lease_seconds=30) is True
    assert two.begin_workspace_update("worker-two", lease_seconds=30) is False
    conversation = two.create_conversation()
    now = time.time()
    snapshot = {"run_id":"blocked","conversation_id":conversation["id"],"goal":"blocked","status":"running","events":[],"created_at":now,"updated_at":now}
    assert two.reserve_run("blocked", conversation["id"], "blocked", snapshot, owner_id="worker-two", lease_seconds=30) is False
    assert one.commit_workspace_revision("worker-one", "rev-b") is True
    assert two.workspace_revision() == "rev-b"
    assert two.reserve_run("allowed", conversation["id"], "allowed", {**snapshot,"run_id":"allowed"}, owner_id="worker-two", lease_seconds=30) is True


def test_active_run_blocks_distributed_workspace_update(tmp_path):
    import time
    path = tmp_path / "workspace-active.db"
    one = WorkspaceStore(path)
    two = WorkspaceStore(path)
    one.ensure_workspace_revision("rev-a")
    conversation = one.create_conversation()
    now = time.time()
    snapshot = {"run_id":"active","conversation_id":conversation["id"],"goal":"active","status":"running","events":[],"created_at":now,"updated_at":now}
    assert one.reserve_run("active", conversation["id"], "active", snapshot, owner_id="worker-one", lease_seconds=30)
    assert two.begin_workspace_update("worker-two", lease_seconds=30) is False


def test_rate_limit_counter_is_shared_across_store_instances(tmp_path):
    path = tmp_path / "rate-limit.db"
    one = WorkspaceStore(path)
    two = WorkspaceStore(path)
    assert one.consume_rate_limit("login:client", limit=2, window_seconds=60, now=100) is True
    assert two.consume_rate_limit("login:client", limit=2, window_seconds=60, now=101) is True
    assert one.consume_rate_limit("login:client", limit=2, window_seconds=60, now=102) is False
    assert two.consume_rate_limit("login:client", limit=2, window_seconds=60, now=161) is True
