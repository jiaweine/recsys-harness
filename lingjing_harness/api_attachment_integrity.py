from __future__ import annotations

import json
import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import File, HTTPException, UploadFile


_PAYLOAD_FILE = re.compile(
    r"^(att-[a-f0-9]{12})(?:\.payload)?\.[a-z0-9]{1,9}$"
)


def install_attachment_integrity_boundary(core: Any) -> None:
    """Keep attachment payload bytes distinct from metadata and recover crash orphans."""

    def gc_attachments(now: float | None = None) -> dict[str, int]:
        now = time.time() if now is None else float(now)
        referenced: set[str] = set()
        removed = 0
        records: list[tuple[float, str, Path, Path, bool]] = []
        managed_targets: set[str] = set()
        has_stale_managed = False

        def file_size(path: Path) -> int:
            try:
                return int(path.stat().st_size)
            except OSError:
                return 0

        with core.ATTACHMENT_LOCK:
            for temp in core.ATTACHMENT_DIR.glob("*.tmp"):
                try:
                    if now - temp.stat().st_mtime > 3600:
                        temp.unlink(missing_ok=True)
                except OSError:
                    continue

            for meta_path in core.ATTACHMENT_DIR.glob("att-*.json"):
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    attachment_id = str(meta.get("id") or "")
                    stored_name = str(meta.get("stored_name") or "")
                    target = core.ATTACHMENT_DIR / stored_name
                    created_at = float(meta.get("created_at") or 0.0)
                    if (
                        not core.ATTACHMENT_ID.fullmatch(attachment_id)
                        or target.parent != core.ATTACHMENT_DIR
                        or target.name != stored_name
                    ):
                        raise ValueError("invalid attachment metadata")
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    meta_path.unlink(missing_ok=True)
                    removed += 1
                    continue

                managed_targets.add(target.name)
                stale = now - created_at > core.ATTACHMENT_ORPHAN_TTL_SECONDS
                has_stale_managed = has_stale_managed or stale
                records.append((created_at, attachment_id, target, meta_path, stale))

            # A process can die after the raw payload is atomically installed but
            # before its metadata file is committed. Recent files are preserved
            # for the normal upload window; stale unmatched payloads are reclaimable
            # without consulting durable message/run history because no valid
            # metadata remains through which the attachment can be loaded.
            for path in core.ATTACHMENT_DIR.iterdir():
                if not path.is_file() or path.name.endswith(".tmp"):
                    continue
                match = _PAYLOAD_FILE.fullmatch(path.name)
                if match is None:
                    continue
                attachment_id = match.group(1)
                if path == core._attachment_meta_path(attachment_id):
                    continue
                if path.name in managed_targets:
                    continue
                try:
                    stale = now - path.stat().st_mtime > core.ATTACHMENT_ORPHAN_TTL_SECONDS
                except OSError:
                    continue
                if stale:
                    path.unlink(missing_ok=True)
                    removed += 1

            # Reference discovery scans historical message payloads and run
            # snapshots, then recursively decodes every matching JSON row. That
            # history read is only required when GC may actually delete a managed
            # attachment. Keep the common recent + under-quota upload path entirely
            # filesystem-local, while preserving the same conservative reference
            # protection whenever TTL or quota eviction is possible.
            total = core._attachment_storage_bytes()
            if has_stale_managed or total > core.MAX_ATTACHMENT_STORAGE_BYTES:
                referenced = core.store.referenced_attachment_ids()

                retained: list[tuple[float, str, Path, Path, bool]] = []
                for created_at, attachment_id, target, meta_path, stale in records:
                    if stale and attachment_id not in referenced:
                        reclaimed = file_size(target) + file_size(meta_path)
                        target.unlink(missing_ok=True)
                        meta_path.unlink(missing_ok=True)
                        removed += 1
                        total = max(0, total - reclaimed)
                        continue
                    retained.append((created_at, attachment_id, target, meta_path, stale))
                records = retained

                # Quota eviction used to rescan the whole attachment directory
                # after every deleted record. Keep the cached total from the first
                # scan and subtract exact reclaimed bytes; one final scan below
                # still observes concurrent filesystem changes before upload.
                if total > core.MAX_ATTACHMENT_STORAGE_BYTES:
                    for _, attachment_id, target, meta_path, _ in sorted(records):
                        if attachment_id in referenced:
                            continue
                        reclaimed = file_size(target) + file_size(meta_path)
                        target.unlink(missing_ok=True)
                        meta_path.unlink(missing_ok=True)
                        removed += 1
                        total = max(0, total - reclaimed)
                        if total <= core.MAX_ATTACHMENT_STORAGE_BYTES:
                            break

            final_total = core._attachment_storage_bytes()
            return {
                "bytes": final_total,
                "removed": removed,
                # This diagnostic count is populated only when a deletion decision
                # required durable-reference discovery; callers use `bytes` for
                # quota enforcement and do not depend on an eager history count.
                "referenced": len(referenced),
            }

    async def upload_attachment(file: UploadFile = File(...)):
        raw = await file.read(core.MAX_ATTACHMENT_BYTES + 1)
        if not raw:
            raise HTTPException(400, "附件不能为空")
        if len(raw) > core.MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "单个附件不能超过 12MB")

        guessed = mimetypes.guess_type(file.filename or "")[0]
        mime = (
            file.content_type or guessed or "application/octet-stream"
        ).lower().split(";", 1)[0].strip()
        if mime not in core.ALLOWED_IMAGE_MIMES and mime not in core.ALLOWED_DOCUMENT_MIMES:
            raise HTTPException(415, "当前支持图片、TXT、Markdown、CSV 和 JSON 附件")

        attachment_id = f"att-{uuid.uuid4().hex[:12]}"
        suffix = Path(file.filename or "").suffix.lower()[:10]
        if not re.fullmatch(r"\.[a-z0-9]{1,9}", suffix or ""):
            suffix = ".bin"

        # `.payload` is structural, not user-controlled. It makes every payload
        # filename disjoint from the durable `att-<id>.json` metadata namespace.
        stored_name = f"{attachment_id}.payload{suffix}"
        target = core.ATTACHMENT_DIR / stored_name
        meta = {
            "id": attachment_id,
            "name": (Path(file.filename or "附件").name or "附件")[:180],
            "mime": mime,
            "size": len(raw),
            "stored_name": stored_name,
            "created_at": time.time(),
        }
        meta_path = core._attachment_meta_path(attachment_id)

        with core.ATTACHMENT_LOCK:
            storage = gc_attachments()
            if storage["bytes"] + len(raw) > core.MAX_ATTACHMENT_STORAGE_BYTES:
                raise HTTPException(507, "附件存储空间已达到上限，请清理未使用附件后再试")

            payload_temp = target.with_suffix(target.suffix + ".tmp")
            payload_temp.write_bytes(raw)
            payload_temp.replace(target)

            meta_temp = meta_path.with_suffix(".json.tmp")
            meta_temp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            meta_temp.replace(meta_path)

        return core._public_attachment(meta)

    for route in list(core.app.router.routes):
        if (
            getattr(route, "path", None) == "/api/attachments"
            and "POST" in (getattr(route, "methods", None) or set())
        ):
            core.app.router.routes.remove(route)

    core._gc_attachments = gc_attachments
    core.upload_attachment = upload_attachment
    core.app.add_api_route(
        "/api/attachments",
        upload_attachment,
        methods=["POST"],
        name="upload_attachment",
    )
