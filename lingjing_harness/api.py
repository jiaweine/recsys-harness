from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import frontend as frontend_package

from .domain import Catalog
from .sample_data import build_sample_catalog
from .store import WorkspaceStore
from .runtime import AgentHarness, AgentMemory, RunCancelled, catalog_fingerprint
from .runtime.perception import PerceptionEngine

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = Path(frontend_package.__file__).resolve().parent
DATA = Path(os.environ.get("LINGJING_DATA_DIR", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
CATALOG_FILE = DATA / "catalog.json"
ATTACHMENT_DIR = DATA / "attachments"
ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
store = WorkspaceStore(DATA / "workspace.db")
memory = AgentMemory(DATA / "agent-memory.db")
perception = PerceptionEngine()


def _load_catalog() -> Catalog:
    if not CATALOG_FILE.exists():
        return build_sample_catalog()
    try:
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        return Catalog.from_payload(payload.get("data", payload), name=payload.get("name", "工作区数据"))
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return build_sample_catalog()


def _persist_catalog(value: Catalog) -> None:
    temp = CATALOG_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"name": value.name, "data": value.to_payload()}, ensure_ascii=False), encoding="utf-8")
    temp.replace(CATALOG_FILE)


catalog = _load_catalog()
harness = AgentHarness(catalog, memory=memory)
WORKSPACE_LOCK = threading.RLock()
CATALOG_REVISION = catalog_fingerprint(catalog)
RUNS: dict[str, dict[str, Any]] = {}
RUN_LOCK = threading.RLock()
ATTACHMENT_LOCK = threading.RLock()
MAX_RUNS = 200
MAX_IMPORT_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024
MAX_ATTACHMENT_STORAGE_BYTES = int(os.environ.get("LINGJING_ATTACHMENT_STORAGE_BYTES", str(512 * 1024 * 1024)))
ATTACHMENT_ORPHAN_TTL_SECONDS = int(os.environ.get("LINGJING_ATTACHMENT_ORPHAN_TTL_SECONDS", "86400"))
ACTIVE_RUN_STATUSES = {"running", "cancel_requested"}
ATTACHMENT_ID = re.compile(r"^att-[a-f0-9]{12}$")
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_DOCUMENT_MIMES = {
    "text/plain", "text/csv", "text/markdown", "text/tab-separated-values",
    "application/json", "application/csv", "application/xml", "application/yaml", "application/x-yaml",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    _gc_attachments()
    await _recover_on_startup()
    yield


app = FastAPI(
    title="灵境体验工作台",
    docs_url="/docs" if os.environ.get("LINGJING_ENV") != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")


class ConversationCreate(BaseModel):
    scene: Literal["search", "recommend", "evolve", "audit"] = "audit"
    title: str = Field(default="新的体验任务", min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title 不能为空")
        return value


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=6000)
    attachments: list[str] = Field(default_factory=list, max_length=8)
    allow_network: bool = False

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content 不能为空")
        return value

    @field_validator("attachments")
    @classmethod
    def validate_attachments(cls, value: list[str]) -> list[str]:
        rows = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if any(not ATTACHMENT_ID.fullmatch(item) for item in rows):
            raise ValueError("附件标识无效")
        return rows[:8]


class ImportPayload(BaseModel):
    name: str = Field(default="导入数据", min_length=1, max_length=120)
    data: dict[str, Any]

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name 不能为空")
        return value


def _prune_runs_locked() -> None:
    if len(RUNS) < MAX_RUNS:
        return
    removable = sorted(
        (row for row in RUNS.values() if row.get("status") in {"completed", "failed", "cancelled"}),
        key=lambda row: row.get("updated_at", 0.0),
    )
    for row in removable[: max(1, len(RUNS) - MAX_RUNS + 1)]:
        RUNS.pop(row["run_id"], None)


def _persist_run(row: dict[str, Any]) -> None:
    store.save_run(
        row["run_id"],
        row["conversation_id"],
        row.get("goal", ""),
        row.get("status", "running"),
        copy.deepcopy(row),
    )


def _attachment_meta_path(attachment_id: str) -> Path:
    return ATTACHMENT_DIR / f"{attachment_id}.json"


def _public_attachment(meta: dict[str, Any], perception_status: str = "pending") -> dict[str, Any]:
    mime = str(meta.get("mime") or "application/octet-stream")
    return {
        "id": meta["id"],
        "name": meta["name"],
        "mime": mime,
        "size": int(meta.get("size") or 0),
        "kind": "image" if mime.startswith("image/") else "document",
        "perception": perception_status,
        "url": f"/api/attachments/{meta['id']}",
    }


def _load_attachment(attachment_id: str) -> dict[str, Any]:
    if not ATTACHMENT_ID.fullmatch(attachment_id):
        raise KeyError(attachment_id)
    meta_path = _attachment_meta_path(attachment_id)
    if not meta_path.exists():
        raise KeyError(attachment_id)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise KeyError(attachment_id) from exc
    path = ATTACHMENT_DIR / str(meta.get("stored_name") or "")
    if not path.exists() or path.parent != ATTACHMENT_DIR:
        raise KeyError(attachment_id)
    return {**meta, "path": str(path)}


def _resolve_attachments(ids: list[str], *, strict: bool = True) -> list[dict[str, Any]]:
    rows = []
    for attachment_id in ids[:8]:
        try:
            rows.append(_load_attachment(attachment_id))
        except KeyError:
            if strict:
                raise HTTPException(400, f"附件不存在或已失效：{attachment_id}")
    return rows


def _attachment_storage_bytes() -> int:
    total = 0
    for path in ATTACHMENT_DIR.iterdir():
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _gc_attachments(now: float | None = None) -> dict[str, int]:
    now = time.time() if now is None else float(now)
    referenced = store.referenced_attachment_ids()
    removed = 0
    records: list[tuple[float, str, Path, Path]] = []
    with ATTACHMENT_LOCK:
        for temp in ATTACHMENT_DIR.glob("*.tmp"):
            try:
                if now - temp.stat().st_mtime > 3600:
                    temp.unlink(missing_ok=True)
            except OSError:
                continue
        for meta_path in ATTACHMENT_DIR.glob("att-*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                attachment_id = str(meta.get("id") or "")
                stored_name = str(meta.get("stored_name") or "")
                target = ATTACHMENT_DIR / stored_name
                created_at = float(meta.get("created_at") or 0.0)
                if not ATTACHMENT_ID.fullmatch(attachment_id) or target.parent != ATTACHMENT_DIR or target.name != stored_name:
                    raise ValueError("invalid attachment metadata")
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                meta_path.unlink(missing_ok=True)
                removed += 1
                continue
            if attachment_id not in referenced and now - created_at > ATTACHMENT_ORPHAN_TTL_SECONDS:
                target.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                removed += 1
                continue
            records.append((created_at, attachment_id, target, meta_path))

        total = _attachment_storage_bytes()
        if total > MAX_ATTACHMENT_STORAGE_BYTES:
            for _, attachment_id, target, meta_path in sorted(records):
                if attachment_id in referenced:
                    continue
                target.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                removed += 1
                total = _attachment_storage_bytes()
                if total <= MAX_ATTACHMENT_STORAGE_BYTES:
                    break
        return {"bytes": _attachment_storage_bytes(), "removed": removed, "referenced": len(referenced)}


async def _perceive_with_cancel(
    rows: list[dict[str, Any]],
    should_stop,
) -> tuple[str, list[dict[str, Any]]]:
    task = asyncio.create_task(asyncio.to_thread(perception.build_context, rows, should_stop=should_stop))
    while not task.done():
        if should_stop():
            task.cancel()
            return "", [_public_attachment(row, perception_status="cancelled") for row in rows]
        await asyncio.sleep(0.1)
    try:
        return task.result()
    except InterruptedError:
        return "", [_public_attachment(row, perception_status="cancelled") for row in rows]


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/status")
def status():
    key = catalog_fingerprint(catalog)
    return {
        "status": "ok",
        "data": catalog.summary(),
        "owned_policy": True,
        "external_model_required": False,
        "autonomous_decision": True,
        "self_evolving": True,
        "memory": memory.stats(key),
        "runtime": {
            "dynamic_replan": True,
            "evidence_utility_controller": True,
            "eval_gated_learning": True,
            "counterfactual_holdout": True,
            "persistent_runs": True,
            "checkpoint_resume": True,
            "idempotent_adaptation": True,
            "bounded_memory": True,
            "automatic_rollback": True,
            "tool_guardrails": True,
        },
        "multimodal": {
            "attachments": True,
            "images": True,
            "documents": True,
            "vision_ready": perception.configured,
        },
        "network": {
            "available": harness.tools.network.configured,
            "permissioned": True,
        },
    }


@app.get("/api/capabilities")
def capabilities():
    return {
        "tools": harness.tools.manifest(),
        "autonomy": {
            "dynamic_replan": True,
            "evidence_utility_controller": True,
            "memory": True,
            "policy_learning": True,
            "self_evolution": True,
            "holdout_validation": True,
            "automatic_rollback": True,
            "checkpoint_resume": True,
            "idempotent_adaptation": True,
        },
        "multimodal": {"attachments": True, "vision_ready": perception.configured},
        "network": {"available": harness.tools.network.configured},
    }


@app.post("/api/attachments")
async def upload_attachment(file: UploadFile = File(...)):
    raw = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not raw:
        raise HTTPException(400, "附件不能为空")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "单个附件不能超过 12MB")
    guessed = mimetypes.guess_type(file.filename or "")[0]
    mime = (file.content_type or guessed or "application/octet-stream").lower().split(";", 1)[0].strip()
    if mime not in ALLOWED_IMAGE_MIMES and mime not in ALLOWED_DOCUMENT_MIMES:
        raise HTTPException(415, "当前支持图片、TXT、Markdown、CSV 和 JSON 附件")
    attachment_id = f"att-{uuid.uuid4().hex[:12]}"
    suffix = Path(file.filename or "").suffix.lower()[:10]
    if not re.fullmatch(r"\.[a-z0-9]{1,9}", suffix or ""):
        suffix = ".bin"
    stored_name = f"{attachment_id}{suffix}"
    target = ATTACHMENT_DIR / stored_name
    with ATTACHMENT_LOCK:
        storage = _gc_attachments()
        if storage["bytes"] + len(raw) > MAX_ATTACHMENT_STORAGE_BYTES:
            raise HTTPException(507, "附件存储空间已达到上限，请清理未使用附件后再试")
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(raw)
        temp.replace(target)
    meta = {
        "id": attachment_id,
        "name": (Path(file.filename or "附件").name or "附件")[:180],
        "mime": mime,
        "size": len(raw),
        "stored_name": stored_name,
        "created_at": time.time(),
    }
    meta_temp = _attachment_meta_path(attachment_id).with_suffix(".json.tmp")
    meta_temp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    meta_temp.replace(_attachment_meta_path(attachment_id))
    return _public_attachment(meta)


@app.get("/api/attachments/{attachment_id}")
def get_attachment(attachment_id: str):
    try:
        meta = _load_attachment(attachment_id)
    except KeyError as exc:
        raise HTTPException(404, "附件不存在") from exc
    return FileResponse(meta["path"], media_type=meta["mime"], filename=meta["name"], content_disposition_type="inline")


@app.get("/api/conversations")
def conversations():
    rows = store.list_conversations()
    with RUN_LOCK:
        active = {row.get("conversation_id") for row in RUNS.values() if row.get("status") in ACTIVE_RUN_STATUSES}
    return [{**row, "active": row["id"] in active} for row in rows]


@app.post("/api/conversations")
def create_conversation(req: ConversationCreate):
    return store.create_conversation(req.title, req.scene)


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str):
    try:
        conversation = store.get_conversation(cid)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    with RUN_LOCK:
        active = next(
            (copy.deepcopy(row) for row in RUNS.values() if row.get("conversation_id") == cid and row.get("status") in ACTIVE_RUN_STATUSES),
            None,
        )
    if active:
        conversation["active_run"] = {"run_id": active["run_id"], "status": active["status"], "events": active.get("events", [])}
    else:
        conversation["active_run"] = None
    return conversation


async def _execute(
    run_id: str,
    cid: str,
    text: str,
    runner: AgentHarness,
    *,
    attachment_ids: list[str] | None = None,
    allow_network: bool = False,
    resume: dict[str, Any] | None = None,
    catalog_revision: str | None = None,
) -> None:
    loop = asyncio.get_running_loop()

    existing = store.assistant_for_job(cid, run_id)
    if existing:
        result = existing.get("payload", {})
        with RUN_LOCK:
            row = RUNS.get(run_id)
            if row is not None:
                row.update({"status": "completed", "result": result, "message": existing, "updated_at": time.time()})
                _persist_run(row)
        return

    def should_stop() -> bool:
        with RUN_LOCK:
            row = RUNS.get(run_id)
            return bool(row and row.get("status") == "cancel_requested")

    attachment_rows = _resolve_attachments(list(attachment_ids or []), strict=False)
    context = ""
    observed_attachments: list[dict[str, Any]] = []
    if attachment_rows:
        context, observed_attachments = await _perceive_with_cancel(attachment_rows, should_stop)
        for row in observed_attachments:
            row["url"] = f"/api/attachments/{row['id']}"
        with RUN_LOCK:
            current = RUNS.get(run_id)
            if current is not None:
                current["attachments"] = observed_attachments
                current["multimodal_context"] = context
                current["updated_at"] = time.time()
                _persist_run(current)

    def sink(event: dict[str, Any]):
        with RUN_LOCK:
            row = RUNS.get(run_id)
            if row is None:
                return
            row["events"].append(event)
            row["updated_at"] = time.time()
            _persist_run(row)

    def checkpoint(payload: dict[str, Any]):
        with RUN_LOCK:
            row = RUNS.get(run_id)
            if row is None:
                return
            row["checkpoint"] = payload
            row["events"] = payload.get("events", row["events"])
            row["updated_at"] = time.time()
            _persist_run(row)

    try:
        result = await loop.run_in_executor(
            None,
            lambda: runner.run(
                text,
                context=context,
                allow_network=allow_network,
                sink=sink,
                checkpoint_sink=checkpoint,
                resume=resume,
                should_stop=should_stop,
            ),
        )
        result["job_id"] = run_id
        result["attachments"] = observed_attachments
        with WORKSPACE_LOCK:
            current_revision = CATALOG_REVISION
        expected_revision = catalog_revision or current_revision
        if expected_revision != current_revision:
            raise RuntimeError("工作区数据已更新，本次旧数据执行结果未写入当前工作区")
        result["catalog_revision"] = expected_revision
        message = store.add_message(cid, "assistant", result["answer"], result)
        with RUN_LOCK:
            row = RUNS.get(run_id)
            if row is not None:
                row.update({"status": "completed", "result": result, "message": message, "updated_at": time.time()})
                _persist_run(row)
    except RunCancelled:
        with RUN_LOCK:
            row = RUNS.get(run_id)
            if row is not None:
                events = list(row.get("events") or [])
                progress = int(events[-1].get("progress", 0)) if events else 0
                events.append({
                    "phase": "cancel",
                    "title": "已停止本次执行",
                    "detail": "已在当前动作结束后停止，不再扩展新的工具调用",
                    "progress": progress,
                    "payload": {},
                    "created_at": time.time(),
                })
                row.update({"status": "cancelled", "events": events, "updated_at": time.time()})
                _persist_run(row)
    except Exception as exc:
        with RUN_LOCK:
            row = RUNS.get(run_id)
            if row is not None:
                row.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "updated_at": time.time()})
                _persist_run(row)


async def _recover_on_startup() -> None:
    for saved in store.recoverable_runs(limit=16):
        run_id = saved["run_id"]
        cid = saved["conversation_id"]
        text = saved["goal"]
        snapshot = saved.get("snapshot") or {}
        snapshot.setdefault("events", [])
        snapshot.setdefault("result", None)
        if saved.get("status") == "cancel_requested":
            snapshot.update({"run_id": run_id, "conversation_id": cid, "goal": text, "status": "cancelled", "updated_at": time.time()})
            snapshot["events"].append({
                "phase": "cancel", "title": "已停止本次执行",
                "detail": "服务恢复时确认了停止请求，任务没有重新执行",
                "progress": int(snapshot["events"][-1].get("progress", 0)) if snapshot["events"] else 0,
                "payload": {"recovered": True}, "created_at": time.time(),
            })
            store.save_run(run_id, cid, text, "cancelled", snapshot)
            continue
        with WORKSPACE_LOCK:
            current_revision = CATALOG_REVISION
        saved_revision = snapshot.get("catalog_revision") or current_revision
        if saved_revision != current_revision:
            snapshot.update({
                "run_id": run_id, "conversation_id": cid, "goal": text, "status": "failed",
                "error": "工作区数据已变化，未恢复旧数据任务", "updated_at": time.time(),
            })
            store.save_run(run_id, cid, text, "failed", snapshot)
            continue
        snapshot.update({"run_id": run_id, "conversation_id": cid, "goal": text, "status": "running", "catalog_revision": saved_revision, "updated_at": time.time()})
        with RUN_LOCK:
            RUNS[run_id] = snapshot
            _persist_run(snapshot)
        asyncio.create_task(
            _execute(
                run_id,
                cid,
                text,
                harness.fork(),
                attachment_ids=list(snapshot.get("attachment_ids") or []),
                allow_network=bool(snapshot.get("allow_network")),
                resume=snapshot.get("checkpoint"),
                catalog_revision=saved_revision,
            )
        )


@app.post("/api/conversations/{cid}/messages")
async def add_message(cid: str, req: ChatRequest):
    try:
        store.get_conversation(cid)
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc

    attachment_rows = _resolve_attachments(req.attachments)
    public_attachments = [_public_attachment(row) for row in attachment_rows]
    run_id = f"job-{uuid.uuid4().hex[:10]}"
    with WORKSPACE_LOCK:
        runner = harness.fork()
        revision = CATALOG_REVISION
    now = time.time()
    row = {
        "run_id": run_id,
        "conversation_id": cid,
        "goal": req.content,
        "status": "running",
        "events": [],
        "result": None,
        "attachment_ids": list(req.attachments),
        "attachments": public_attachments,
        "allow_network": req.allow_network,
        "catalog_revision": revision,
        "created_at": now,
        "updated_at": now,
    }
    with RUN_LOCK:
        if any(existing.get("conversation_id") == cid and existing.get("status") in ACTIVE_RUN_STATUSES for existing in RUNS.values()):
            raise HTTPException(409, "当前任务仍在执行，请等待完成或切换到另一个任务")
        user = store.add_message(
            cid,
            "user",
            req.content,
            {"attachments": public_attachments, "allow_network": req.allow_network},
        )
        _prune_runs_locked()
        RUNS[run_id] = row
        _persist_run(row)
    asyncio.create_task(
        _execute(
            run_id,
            cid,
            req.content,
            runner,
            attachment_ids=list(req.attachments),
            allow_network=req.allow_network,
            catalog_revision=revision,
        )
    )
    return {"status": "accepted", "run_id": run_id, "message": user}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    with RUN_LOCK:
        row = RUNS.get(run_id)
        if row is None:
            try:
                saved = store.get_run(run_id)
            except KeyError as exc:
                raise HTTPException(404, "执行任务不存在") from exc
            status = saved.get("status")
            if status == "cancelled":
                return {"ok": True, "status": "cancelled"}
            raise HTTPException(409, "该执行已经结束，无法停止")
        status = row.get("status")
        if status == "cancelled":
            return {"ok": True, "status": "cancelled"}
        if status not in ACTIVE_RUN_STATUSES:
            raise HTTPException(409, "该执行已经结束，无法停止")
        row["status"] = "cancel_requested"
        row["updated_at"] = time.time()
        _persist_run(row)
    return {"ok": True, "status": "cancel_requested"}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    with RUN_LOCK:
        row = RUNS.get(run_id)
        snapshot = copy.deepcopy(row) if row is not None else None
    if snapshot is None:
        try:
            snapshot = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "执行任务不存在") from exc
    return snapshot


@app.post("/api/data/import")
def import_data(req: ImportPayload):
    global catalog, harness, CATALOG_REVISION
    try:
        new = Catalog.from_payload(req.data, name=req.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with WORKSPACE_LOCK, RUN_LOCK:
        if any(row.get("status") in ACTIVE_RUN_STATUSES for row in RUNS.values()):
            raise HTTPException(409, "仍有任务在执行，请停止或等待完成后再更换工作区数据")
        _persist_catalog(new)
        catalog = new
        harness = AgentHarness(catalog, memory=memory)
        CATALOG_REVISION = catalog_fingerprint(catalog)
    return {"ok": True, "data": catalog.summary(), "catalog_revision": CATALOG_REVISION}


@app.post("/api/data/import-file")
async def import_file(file: UploadFile = File(...)):
    global catalog, harness, CATALOG_REVISION
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "文件不能超过 8MB")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "当前只支持 UTF-8 JSON 数据文件") from exc
    try:
        new = Catalog.from_payload(payload, name=file.filename or "导入数据")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with WORKSPACE_LOCK, RUN_LOCK:
        if any(row.get("status") in ACTIVE_RUN_STATUSES for row in RUNS.values()):
            raise HTTPException(409, "仍有任务在执行，请停止或等待完成后再更换工作区数据")
        _persist_catalog(new)
        catalog = new
        harness = AgentHarness(catalog, memory=memory)
        CATALOG_REVISION = catalog_fingerprint(catalog)
    return {"ok": True, "data": catalog.summary(), "catalog_revision": CATALOG_REVISION}
