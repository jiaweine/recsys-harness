from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "mean_ms": round(statistics.fmean(values), 3) if values else 0.0,
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def _seed_messages(store, conversation_id: str, messages: int, referenced_ids: list[str]) -> None:
    rows = []
    for index in range(messages):
        payload = {"blob": "x" * 512, "index": index}
        if index < len(referenced_ids):
            payload["attachments"] = [{"id": referenced_ids[index]}]
        rows.append(
            (
                f"gc-msg-{index}",
                conversation_id,
                "user" if index % 2 == 0 else "assistant",
                f"gc benchmark message {index}",
                json.dumps(payload, ensure_ascii=False),
                float(index + 1),
            )
        )
    with sqlite3.connect(store.path) as connection:
        connection.executemany(
            "insert into messages(id,conversation_id,role,content,payload,created_at) "
            "values(?,?,?,?,?,?)",
            rows,
        )
        connection.commit()


def _seed_attachments(api_module, attachments: int) -> list[str]:
    ids: list[str] = []
    now = time.time()
    for index in range(attachments):
        attachment_id = f"att-{index:012x}"
        ids.append(attachment_id)
        stored_name = f"{attachment_id}.txt"
        target = api_module.ATTACHMENT_DIR / stored_name
        target.write_bytes(b"x" * 1024)
        meta = {
            "id": attachment_id,
            "name": f"attachment-{index}.txt",
            "mime": "text/plain",
            "size": 1024,
            "stored_name": stored_name,
            "created_at": now,
        }
        (api_module.ATTACHMENT_DIR / f"{attachment_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False),
            encoding="utf-8",
        )
    return ids


def run_benchmark(*, messages: int, attachments: int, repeats: int) -> dict[str, object]:
    import lingjing_harness.api as api_module

    conversation = api_module.store.create_conversation("attachment-gc-benchmark", "audit")
    ids = _seed_attachments(api_module, attachments)
    referenced_ids = ids[: min(64, len(ids))]
    _seed_messages(
        api_module.store,
        conversation["id"],
        messages,
        referenced_ids,
    )

    # Warm page cache and directory metadata before measuring.
    api_module.store.referenced_attachment_ids()
    api_module._attachment_storage_bytes()
    api_module._gc_attachments()

    reference_scan = _timed(
        api_module.store.referenced_attachment_ids,
        repeats,
    )
    storage_scan = _timed(
        api_module._attachment_storage_bytes,
        repeats * 4,
    )
    full_gc = _timed(
        api_module._gc_attachments,
        repeats,
    )
    api_module._gc_attachments()
    upload_fast_check = _timed(
        lambda: api_module._attachment_storage_for_upload(1024),
        repeats * 4,
    )

    referenced = api_module.store.referenced_attachment_ids()
    if not set(referenced_ids).issubset(referenced):
        raise AssertionError("attachment reference scan lost durable references")

    return {
        "messages": messages,
        "attachments": attachments,
        "referenced": len(referenced),
        "reference_scan": _summary(reference_scan),
        "storage_scan": _summary(storage_scan),
        "full_gc": _summary(full_gc),
        "upload_fast_check": _summary(upload_fast_check),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark attachment GC database and filesystem hot paths."
    )
    parser.add_argument("--messages", type=int, default=100_000)
    parser.add_argument("--attachments", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=8)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="xushu-attachment-gc-") as directory:
        os.environ["LINGJING_DATA_DIR"] = str(Path(directory))
        os.environ["LINGJING_ENV"] = "development"
        result = run_benchmark(
            messages=max(10_000, args.messages),
            attachments=max(10, args.attachments),
            repeats=max(3, args.repeats),
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
