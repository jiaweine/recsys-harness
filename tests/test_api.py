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
