from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

import lingjing_harness.api as api_module
from lingjing_harness.api import app


def test_json_attachment_payload_round_trips_without_metadata_collision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(api_module, "ATTACHMENT_DIR", tmp_path)
    payload = b'{"query":"camping lantern","note":"top result looks weak"}'

    with TestClient(app) as client:
        uploaded = client.post(
            "/api/attachments",
            files={"file": ("context.json", payload, "application/json")},
        )
        assert uploaded.status_code == 200
        attachment_id = uploaded.json()["id"]

        loaded = api_module._load_attachment(attachment_id)
        assert Path(loaded["path"]).read_bytes() == payload

        downloaded = client.get(f"/api/attachments/{attachment_id}")
        assert downloaded.status_code == 200
        assert downloaded.content == payload


def test_gc_collects_stale_raw_only_crash_orphan_but_keeps_recent_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(api_module, "ATTACHMENT_DIR", tmp_path)
    now = time.time()
    stale = tmp_path / "att-deadbeefcafe.txt"
    recent = tmp_path / "att-cafebabefeed.txt"
    stale.write_bytes(b"stale crash orphan")
    recent.write_bytes(b"recent upload in metadata window")
    stale_time = now - api_module.ATTACHMENT_ORPHAN_TTL_SECONDS - 2
    os.utime(stale, (stale_time, stale_time))

    stats = api_module._gc_attachments(now=now)

    assert not stale.exists()
    assert recent.exists()
    assert stats["removed"] >= 1


def test_gc_quota_eviction_keeps_directory_storage_scans_constant(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(api_module, "ATTACHMENT_DIR", tmp_path)
    monkeypatch.setattr(api_module, "MAX_ATTACHMENT_STORAGE_BYTES", 0)
    monkeypatch.setattr(api_module.store, "referenced_attachment_ids", lambda: set())
    now = time.time()

    attachment_ids = []
    for index in range(8):
        attachment_id = f"att-{index:012x}"
        attachment_ids.append(attachment_id)
        stored_name = f"{attachment_id}.payload.txt"
        (tmp_path / stored_name).write_bytes(b"payload")
        (tmp_path / f"{attachment_id}.json").write_text(
            json.dumps(
                {
                    "id": attachment_id,
                    "name": f"fixture-{index}.txt",
                    "mime": "text/plain",
                    "size": 7,
                    "stored_name": stored_name,
                    "created_at": now - index,
                }
            ),
            encoding="utf-8",
        )

    original_storage_bytes = api_module._attachment_storage_bytes
    storage_scans = 0

    def counted_storage_bytes() -> int:
        nonlocal storage_scans
        storage_scans += 1
        return original_storage_bytes()

    monkeypatch.setattr(api_module, "_attachment_storage_bytes", counted_storage_bytes)
    stats = api_module._gc_attachments(now=now)

    assert stats["removed"] == len(attachment_ids)
    assert stats["bytes"] == 0
    assert storage_scans == 2
    assert list(tmp_path.iterdir()) == []
