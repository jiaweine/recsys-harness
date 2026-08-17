import os, tempfile
os.environ["LINGJING_DATA_DIR"] = tempfile.mkdtemp(prefix="lingjing-recsys-api-")

from fastapi.testclient import TestClient
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
    assert c.post('/api/conversations',json={"scene":"search","title":"x"*121}).status_code==422


def test_message_run_completes_and_persists_assistant_result():
    import time
    with TestClient(app) as c:
        conv=c.post('/api/conversations',json={"scene":"search","title":"run"}).json()
        accepted=c.post(f"/api/conversations/{conv['id']}/messages",json={"content":"搜索‘露营灯’，帮我检查"})
        assert accepted.status_code==200
        run_id=accepted.json()["run_id"]
        row=None
        for _ in range(100):
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
