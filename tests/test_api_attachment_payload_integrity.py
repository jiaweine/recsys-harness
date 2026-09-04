from __future__ import annotations

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
