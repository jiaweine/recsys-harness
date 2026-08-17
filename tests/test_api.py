import os, tempfile
os.environ["LINGJING_DATA_DIR"] = tempfile.mkdtemp(prefix="lingjing-recsys-api-")

import asyncio
import time

from fastapi.testclient import TestClient
import lingjing_harness.api as api_module
from lingjing_harness.api import app


def test_status_and_conversation():
    c=TestClient(app)
    status=c.get('/api/status')
    assert status.status_code==200
    assert status.json()["owned_policy"] is True
    conv=c.post('/api/conversations',json={"scene":"search","title":"test"})
    assert conv.status_code==200
    assert c.get(f"/api/conversations/{conv.json()['id']}").status_code==200


def test_import_rejects_empty_catalog():
    c=TestClient(app)
    r=c.post('/api/data/import',json={"name":"bad","data":{"items":[]}})
    assert r.status_code==400


def test_import_rejects_invalid_shapes_and_non_finite_values():
    c=TestClient(app, raise_server_exceptions=False)
    bad_shape=c.post('/api/data/import',json={"name":"bad","data":{"items":"not-a-list"}})
    assert bad_shape.status_code==400
    assert "items 必须是数组" in bad_shape.json()["detail"]

    non_finite=c.post('/api/data/import',json={"name":"bad","data":{"items":[{"id":"a","title":"A","popularity":"Infinity"}]}})
    assert non_finite.status_code==400
    assert "有限数值" in non_finite.json()["detail"]


def test_import_rejects_catalog_with_no_valid_items():
    c=TestClient(app)
    r=c.post('/api/data/import',json={"name":"bad","data":{"items":[{"id":"","title":""}]}})
    assert r.status_code==400


def test_conversation_contract_rejects_unknown_scene_and_huge_title():
    c=TestClient(app)
    assert c.post('/api/conversations',json={"scene":"weird","title":"x"}).status_code==422
    assert c.post('/api/conversations',json={"scene":"evolve","title":"x"}).status_code==200
    assert c.post('/api/conversations',json={"scene":"search","title":"x"*121}).status_code==422


def test_message_run_completes_and_persists_assistant_result():
    with TestClient(app) as c:
        conv=c.post('/api/conversations',json={"scene":"search","title":"run"}).json()
        accepted=c.post(f"/api/conversations/{conv['id']}/messages",json={"content":"搜索‘露营灯’，帮我检查"})
        assert accepted.status_code==200
        run_id=accepted.json()["run_id"]
        row=None
        for _ in range(150):
            row=c.get(f"/api/runs/{run_id}").json()
            if row["status"] in {"completed","failed"}:
                break
            time.sleep(.02)
        assert row is not None and row["status"]=="completed"
        assert row["result"]["events"][-1]["progress"]==100
        loaded=c.get(f"/api/conversations/{conv['id']}").json()
        assert [m["role"] for m in loaded["messages"]][-2:] == ["user","assistant"]


def test_message_rejects_whitespace_only_content():
    c=TestClient(app)
    conv=c.post('/api/conversations',json={"scene":"search","title":"x"}).json()
    assert c.post(f"/api/conversations/{conv['id']}/messages",json={"content":"   \n  "}).status_code==422


def test_file_import_rejects_non_object_json_and_oversized_payload():
    c=TestClient(app)
    not_object=c.post('/api/data/import-file',files={'file':('bad.json',b'[]','application/json')})
    assert not_object.status_code==400
    assert '顶层必须是 JSON 对象' in not_object.json()['detail']

    too_big=b'{' + b' '*(8*1024*1024) + b'}'
    oversized=c.post('/api/data/import-file',files={'file':('huge.json',too_big,'application/json')})
    assert oversized.status_code==413


def test_multimodal_attachment_is_persisted_and_enters_run_context():
    with TestClient(app) as c:
        uploaded=c.post('/api/attachments',files={'file':('context.json',b'{"query":"\xe9\x9c\xb2\xe8\x90\xa5\xe7\x81\xaf","note":"top result looks weak"}','application/json')})
        assert uploaded.status_code==200
        attachment=uploaded.json()
        assert attachment['kind']=='document'
        conv=c.post('/api/conversations',json={"scene":"search","title":"multi"}).json()
        accepted=c.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content":"结合附件检查搜索‘露营灯’的体验","attachments":[attachment['id']]},
        )
        assert accepted.status_code==200
        run_id=accepted.json()['run_id']
        row=None
        for _ in range(150):
            row=c.get(f'/api/runs/{run_id}').json()
            if row['status'] in {'completed','failed'}:
                break
            time.sleep(.02)
        assert row and row['status']=='completed'
        assert row['result']['multimodal']['context_used'] is True
        assert row['result']['attachments'][0]['id']==attachment['id']
        loaded=c.get(f"/api/conversations/{conv['id']}").json()
        user=next(m for m in loaded['messages'] if m['role']=='user')
        assert user['payload']['attachments'][0]['id']==attachment['id']


def test_attachment_rejects_unsupported_type_and_large_file():
    c=TestClient(app)
    unsupported=c.post('/api/attachments',files={'file':('x.exe',b'abc','application/octet-stream')})
    assert unsupported.status_code==415
    huge=b'x'*(12*1024*1024+1)
    oversized=c.post('/api/attachments',files={'file':('huge.txt',huge,'text/plain')})
    assert oversized.status_code==413


def test_same_conversation_rejects_parallel_run_but_other_conversation_is_allowed(monkeypatch):
    async def slow_execute(*args, **kwargs):
        await asyncio.sleep(.2)

    monkeypatch.setattr(api_module, '_execute', slow_execute)
    with TestClient(app) as c:
        one=c.post('/api/conversations',json={"scene":"search","title":"one"}).json()
        two=c.post('/api/conversations',json={"scene":"recommend","title":"two"}).json()
        first=c.post(f"/api/conversations/{one['id']}/messages",json={"content":"检查搜索体验"})
        assert first.status_code==200
        active=c.get(f"/api/conversations/{one['id']}").json()
        assert active['active_run']['run_id']==first.json()['run_id']
        duplicate=c.post(f"/api/conversations/{one['id']}/messages",json={"content":"再检查一次"})
        assert duplicate.status_code==409
        parallel=c.post(f"/api/conversations/{two['id']}/messages",json={"content":"检查推荐体验"})
        assert parallel.status_code==200


def test_status_and_capabilities_expose_autonomous_runtime():
    c = TestClient(app)
    status = c.get('/api/status').json()
    assert status["autonomous_decision"] is True
    assert status["self_evolving"] is True
    assert status["runtime"]["evidence_utility_controller"] is True
    assert status["runtime"]["eval_gated_learning"] is True
    assert status["runtime"]["checkpoint_resume"] is True
    assert status["runtime"]["idempotent_adaptation"] is True
    assert status["runtime"]["automatic_rollback"] is True
    assert status["multimodal"]["attachments"] is True
    assert "available" in status["network"]
    capabilities = c.get('/api/capabilities')
    assert capabilities.status_code == 200
    body = capabilities.json()
    assert body["autonomy"]["dynamic_replan"] is True
    assert body["autonomy"]["evidence_utility_controller"] is True
    assert body["autonomy"]["holdout_validation"] is True
    assert body["autonomy"]["checkpoint_resume"] is True
    assert body["multimodal"]["attachments"] is True
    assert any(tool["risk"] == "adaptive" for tool in body["tools"])


def test_running_task_can_be_stopped_and_conversation_becomes_available(monkeypatch):
    from lingjing_harness.runtime import RunCancelled

    def cancellable_run(self, text, *, should_stop=None, **kwargs):
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if should_stop and should_stop():
                raise RunCancelled("stopped")
            time.sleep(.01)
        raise AssertionError("cancel signal was not delivered")

    monkeypatch.setattr(api_module.AgentHarness, "run", cancellable_run)
    with TestClient(app) as c:
        conv = c.post('/api/conversations', json={"scene": "search", "title": "cancel"}).json()
        accepted = c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "检查搜索体验"})
        assert accepted.status_code == 200
        run_id = accepted.json()["run_id"]

        stopped = c.post(f"/api/runs/{run_id}/cancel", json={})
        assert stopped.status_code == 200
        assert stopped.json()["status"] in {"cancel_requested", "cancelled"}

        row = None
        for _ in range(120):
            row = c.get(f"/api/runs/{run_id}").json()
            if row["status"] == "cancelled":
                break
            time.sleep(.01)
        assert row and row["status"] == "cancelled"
        assert row["events"][-1]["phase"] == "cancel"

        conversation = c.get(f"/api/conversations/{conv['id']}").json()
        assert conversation["active_run"] is None
        again = c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "重新检查"})
        assert again.status_code == 200
        c.post(f"/api/runs/{again.json()['run_id']}/cancel", json={})


def test_cancel_requested_run_is_finalized_on_restart():
    run_id = "job-cancel-recovery"
    conv = api_module.store.create_conversation("cancel recovery", "search")
    snapshot = {
        "run_id": run_id, "conversation_id": conv["id"], "goal": "停止这个任务",
        "status": "cancel_requested", "events": [], "created_at": time.time(), "updated_at": time.time(),
    }
    api_module.store.save_run(run_id, conv["id"], snapshot["goal"], "cancel_requested", snapshot)
    with api_module.RUN_LOCK:
        api_module.RUNS.pop(run_id, None)
    asyncio.run(api_module._recover_on_startup())
    saved = api_module.store.get_run(run_id)
    assert saved["status"] == "cancelled"
    assert saved["events"][-1]["phase"] == "cancel"
    assert saved["events"][-1]["payload"]["recovered"] is True


def test_workspace_import_is_blocked_while_a_run_is_active():
    c = TestClient(app)
    run_id = "job-import-guard"
    with api_module.RUN_LOCK:
        api_module.RUNS[run_id] = {
            "run_id": run_id, "conversation_id": "guard", "goal": "guard",
            "status": "running", "events": [], "created_at": time.time(), "updated_at": time.time(),
        }
    try:
        blocked = c.post('/api/data/import', json={"name": "replacement", "data": api_module.catalog.to_payload()})
        assert blocked.status_code == 409
        assert "仍有任务在执行" in blocked.json()["detail"]
    finally:
        with api_module.RUN_LOCK:
            api_module.RUNS.pop(run_id, None)


def test_orphan_attachment_is_collected_after_ttl():
    with TestClient(app) as c:
        uploaded = c.post('/api/attachments', files={'file':('orphan.txt', b'orphan', 'text/plain')}).json()
    meta_path = api_module._attachment_meta_path(uploaded['id'])
    meta = api_module.json.loads(meta_path.read_text(encoding='utf-8'))
    meta['created_at'] = time.time() - api_module.ATTACHMENT_ORPHAN_TTL_SECONDS - 2
    target = api_module.ATTACHMENT_DIR / meta['stored_name']
    meta_path.write_text(api_module.json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    stats = api_module._gc_attachments()
    assert stats['removed'] >= 1
    assert not meta_path.exists()
    assert not target.exists()


def test_stop_request_does_not_wait_for_slow_perception(monkeypatch):
    def slow_perception(rows, **kwargs):
        time.sleep(1.0)
        return '', []

    monkeypatch.setattr(api_module.perception, 'build_context', slow_perception)
    with TestClient(app) as c:
        uploaded = c.post('/api/attachments', files={'file':('slow.txt', b'context', 'text/plain')}).json()
        conv = c.post('/api/conversations', json={'scene':'search','title':'perception stop'}).json()
        accepted = c.post(
            f"/api/conversations/{conv['id']}/messages",
            json={'content':'检查附件并停止','attachments':[uploaded['id']]},
        ).json()
        c.post(f"/api/runs/{accepted['run_id']}/cancel", json={})
        row = None
        for _ in range(40):
            row = c.get(f"/api/runs/{accepted['run_id']}").json()
            if row['status'] == 'cancelled':
                break
            time.sleep(.02)
        assert row and row['status'] == 'cancelled'


def test_cancel_can_land_on_a_different_worker():
    with TestClient(app) as c:
        conv = c.post('/api/conversations', json={"scene":"search","title":"remote cancel"}).json()
        run_id = "job-remote-cancel"
        now = time.time()
        snapshot = {
            "run_id": run_id, "conversation_id": conv["id"], "goal": "remote",
            "status": "running", "events": [], "created_at": now, "updated_at": now,
        }
        assert api_module.store.reserve_run(
            run_id, conv["id"], "remote", snapshot, owner_id="other-worker", lease_seconds=30
        )
        with api_module.RUN_LOCK:
            api_module.RUNS.pop(run_id, None)
        response = c.post(f'/api/runs/{run_id}/cancel', json={})
        assert response.status_code == 200
        assert response.json()['status'] == 'cancel_requested'
        assert api_module.store.run_status(run_id) == 'cancel_requested'
        final = {**snapshot, "status":"cancelled", "updated_at":time.time()}
        api_module.store.save_run(run_id, conv["id"], "remote", "cancelled", final)
