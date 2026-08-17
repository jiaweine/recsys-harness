from __future__ import annotations

import asyncio, json, os, threading, time, uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .domain import Catalog
from .sample_data import build_sample_catalog
from .store import WorkspaceStore
from .runtime import AgentHarness

ROOT=Path(__file__).resolve().parents[1]
FRONTEND=ROOT/"frontend"
DATA=Path(os.environ.get("LINGJING_DATA_DIR",ROOT/"data"))
DATA.mkdir(parents=True,exist_ok=True)
store=WorkspaceStore(DATA/"workspace.db")
catalog=build_sample_catalog()
harness=AgentHarness(catalog)
RUNS:dict[str,dict[str,Any]]={}
RUN_LOCK=threading.RLock()

app=FastAPI(title="灵境体验工作台",docs_url="/docs" if os.environ.get("LINGJING_ENV")!="production" else None,redoc_url=None)
app.mount("/assets",StaticFiles(directory=FRONTEND),name="assets")

class ConversationCreate(BaseModel):
    scene:str="audit"
    title:str="新的体验任务"

class ChatRequest(BaseModel):
    content:str=Field(min_length=1,max_length=6000)

class ImportPayload(BaseModel):
    name:str="导入数据"
    data:dict[str,Any]

@app.get("/")
def index():
    return FileResponse(FRONTEND/"index.html")

@app.get("/api/status")
def status():
    return {"status":"ok","data":catalog.summary(),"owned_policy":True,"external_model_required":False}

@app.get("/api/conversations")
def conversations():
    return store.list_conversations()

@app.post("/api/conversations")
def create_conversation(req:ConversationCreate):
    return store.create_conversation(req.title,req.scene)

@app.get("/api/conversations/{cid}")
def get_conversation(cid:str):
    try:
        return store.get_conversation(cid)
    except KeyError:
        raise HTTPException(404,"任务不存在")

async def _execute(run_id:str,cid:str,text:str)->None:
    loop=asyncio.get_running_loop()
    def sink(ev:dict[str,Any]):
        with RUN_LOCK:
            RUNS[run_id]["events"].append(ev)
            RUNS[run_id]["updated_at"]=time.time()
    try:
        result=await loop.run_in_executor(None,lambda:harness.run(text,sink=sink))
        msg=store.add_message(cid,"assistant",result["answer"],result)
        with RUN_LOCK:
            RUNS[run_id].update({"status":"completed","result":result,"message":msg,"updated_at":time.time()})
    except Exception as exc:
        with RUN_LOCK:
            RUNS[run_id].update({"status":"failed","error":f"{type(exc).__name__}: {exc}","updated_at":time.time()})

@app.post("/api/conversations/{cid}/messages")
async def add_message(cid:str,req:ChatRequest):
    try:
        store.get_conversation(cid)
    except KeyError:
        raise HTTPException(404,"任务不存在")
    user=store.add_message(cid,"user",req.content,{})
    run_id=f"job-{uuid.uuid4().hex[:10]}"
    with RUN_LOCK:
        RUNS[run_id]={"run_id":run_id,"conversation_id":cid,"status":"running","events":[],"result":None,"created_at":time.time(),"updated_at":time.time()}
    asyncio.create_task(_execute(run_id,cid,req.content))
    return {"status":"accepted","run_id":run_id,"message":user}

@app.get("/api/runs/{run_id}")
def get_run(run_id:str):
    with RUN_LOCK:
        row=RUNS.get(run_id)
    if not row:
        raise HTTPException(404,"执行任务不存在")
    return row

@app.post("/api/data/import")
def import_data(req:ImportPayload):
    global catalog,harness
    try:
        new=Catalog.from_payload(req.data,name=req.name)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    catalog=new
    harness=AgentHarness(catalog)
    return {"ok":True,"data":catalog.summary()}

@app.post("/api/data/import-file")
async def import_file(file:UploadFile=File(...)):
    global catalog,harness
    raw=await file.read()
    if len(raw)>8*1024*1024:
        raise HTTPException(413,"文件不能超过 8MB")
    try:
        payload=json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(400,"当前只支持 UTF-8 JSON 数据文件") from exc
    try:
        new=Catalog.from_payload(payload,name=file.filename or "导入数据")
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    catalog=new
    harness=AgentHarness(catalog)
    return {"ok":True,"data":catalog.summary()}
