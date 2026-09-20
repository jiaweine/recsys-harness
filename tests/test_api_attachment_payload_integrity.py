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


def test_gc_recent_under_quota_managed_attachment_skips_reference_history_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(api_module, "ATTACHMENT_DIR", tmp_path)
    now = time.time()
    attachment_id = "att-111111111111"
    stored_name = f"{attachment_id}.payload.txt"
    payload_path = tmp_path / stored_name
    meta_path = tmp_path / f"{attachment_id}.json"
    payload_path.write_bytes(b"recent payload")
    meta_path.write_text(
        json.dumps(
            {
                "id": attachment_id,
                "name": "recent.txt",
                "mime": "text/plain",
                "size": 14,
                "stored_name": stored_name,
                "created_at": now,
            }
        ),
        encoding="utf-8",
    )

    def history_scan_is_forbidden() -> set[str]:
        raise AssertionError("under-quota GC must not scan durable attachment history")

    monkeypatch.setattr(
        api_module.store,
        "referenced_attachment_ids",
        history_scan_is_forbidden,
    )

    stats = api_module._gc_attachments(now=now)

    assert payload_path.exists()
    assert meta_path.exists()
    assert stats["removed"] == 0
    assert stats["referenced"] == 0


def test_gc_stale_managed_attachment_still_loads_references_before_delete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(api_module, "ATTACHMENT_DIR", tmp_path)
    now = time.time()
    attachment_id = "att-222222222222"
    stored_name = f"{attachment_id}.payload.txt"
    payload_path = tmp_path / stored_name
    meta_path = tmp_path / f"{attachment_id}.json"
    payload_path.write_bytes(b"historical payload")
    meta_path.write_text(
        json.dumps(
            {
                "id": attachment_id,
                "name": "historical.txt",
                "mime": "text/plain",
                "size": 18,
                "stored_name": stored_name,
                "created_at": now - api_module.ATTACHMENT_ORPHAN_TTL_SECONDS - 5,
            }
        ),
        encoding="utf-8",
    )
    reference_scans = 0

    def referenced_attachment_ids() -> set[str]:
        nonlocal reference_scans
        reference_scans += 1
        return {attachment_id}

    monkeypatch.setattr(
        api_module.store,
        "referenced_attachment_ids",
        referenced_attachment_ids,
    )

    stats = api_module._gc_attachments(now=now)

    assert reference_scans == 1
    assert payload_path.exists()
    assert meta_path.exists()
    assert stats["removed"] == 0
    assert stats["referenced"] == 1


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



def test_upload_storage_check_skips_full_gc_until_interval_expires(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(api_module, "ATTACHMENT_DIR", tmp_path)
    now = 2_000_000.0
    stale = tmp_path / "att-deadbeefcafe.payload.txt"
    stale.write_bytes(b"stale raw orphan")
    stale_time = now - api_module.ATTACHMENT_ORPHAN_TTL_SECONDS - 5
    os.utime(stale, (stale_time, stale_time))

    api_module._ATTACHMENT_GC_STATE["last_full_gc_at"] = now
    quick = api_module._attachment_storage_for_upload(1, now=now + 1)

    assert stale.exists()
    assert quick["removed"] == 0

    due = api_module._attachment_storage_for_upload(
        1,
        now=now + api_module.ATTACHMENT_GC_INTERVAL_SECONDS + 1,
    )

    assert not stale.exists()
    assert due["removed"] >= 1


def test_upload_storage_check_forces_full_gc_under_capacity_pressure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(api_module, "ATTACHMENT_DIR", tmp_path)
    now = 3_000_000.0
    stale = tmp_path / "att-cafebabefeed.payload.txt"
    stale.write_bytes(b"reclaimable")
    stale_time = now - api_module.ATTACHMENT_ORPHAN_TTL_SECONDS - 5
    os.utime(stale, (stale_time, stale_time))

    monkeypatch.setattr(api_module, "MAX_ATTACHMENT_STORAGE_BYTES", 5)
    api_module._ATTACHMENT_GC_STATE["last_full_gc_at"] = now

    storage = api_module._attachment_storage_for_upload(1, now=now + 1)

    assert not stale.exists()
    assert storage["removed"] >= 1
    assert storage["bytes"] == 0


def test_upload_storage_check_uses_one_filesystem_scan_on_fast_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(api_module, "ATTACHMENT_DIR", tmp_path)
    now = 4_000_000.0
    (tmp_path / "att-111111111111.payload.txt").write_bytes(b"payload")

    original_storage_bytes = api_module._attachment_storage_bytes
    scans = 0

    def counted_storage_bytes() -> int:
        nonlocal scans
        scans += 1
        return original_storage_bytes()

    monkeypatch.setattr(api_module, "_attachment_storage_bytes", counted_storage_bytes)
    api_module._ATTACHMENT_GC_STATE["last_full_gc_at"] = now

    storage = api_module._attachment_storage_for_upload(1, now=now + 1)

    assert storage["bytes"] == 7
    assert storage["removed"] == 0
    assert scans == 1
