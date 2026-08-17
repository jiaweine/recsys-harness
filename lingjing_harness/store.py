from __future__ import annotations

import json, sqlite3, threading, time, uuid
from pathlib import Path
from typing import Any


class WorkspaceStore:
    def __init__(self,path:str|Path)->None:
        self.path=str(path)
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        self._lock=threading.RLock()
        self._init()

    def _connect(self):
        c=sqlite3.connect(self.path,check_same_thread=False)
        c.row_factory=sqlite3.Row
        return c

    def _init(self)->None:
        sql = """
        create table if not exists conversations(id text primary key,title text not null,scene text not null,created_at real not null,updated_at real not null);
        create table if not exists messages(id text primary key,conversation_id text not null,role text not null,content text not null,payload text not null,created_at real not null);
        create index if not exists idx_messages_conversation on messages(conversation_id,created_at);
        create table if not exists runs(run_id text primary key,conversation_id text not null,goal text not null,status text not null,snapshot text not null,created_at real not null,updated_at real not null);
        create index if not exists idx_runs_status on runs(status,updated_at);
        """
        with self._connect() as c:
            c.executescript(sql)

    def list_conversations(self,limit:int=40)->list[dict[str,Any]]:
        with self._connect() as c:
            rows=c.execute("select * from conversations order by updated_at desc limit ?",(limit,)).fetchall()
        return [dict(x) for x in rows]

    def create_conversation(self,title:str="新的体验任务",scene:str="audit")->dict[str,Any]:
        now=time.time(); cid=f"cv-{uuid.uuid4().hex[:10]}"
        with self._connect() as c:
            c.execute("insert into conversations values(?,?,?,?,?)",(cid,title,scene,now,now))
        return {"id":cid,"title":title,"scene":scene,"created_at":now,"updated_at":now}

    def get_conversation(self,cid:str)->dict[str,Any]:
        with self._connect() as c:
            row=c.execute("select * from conversations where id=?",(cid,)).fetchone()
        if not row: raise KeyError(cid)
        return {**dict(row),"messages":self.list_messages(cid)}

    def list_messages(self,cid:str)->list[dict[str,Any]]:
        with self._connect() as c:
            rows=c.execute("select * from messages where conversation_id=? order by created_at",(cid,)).fetchall()
        out=[]
        for row in rows:
            d=dict(row); d["payload"]=json.loads(d.pop("payload") or "{}"); out.append(d)
        return out

    def add_message(self,cid:str,role:str,content:str,payload:dict|None=None)->dict[str,Any]:
        mid=f"msg-{uuid.uuid4().hex[:12]}"; now=time.time(); payload=payload or {}
        with self._connect() as c:
            if role=="user":
                count=c.execute("select count(*) from messages where conversation_id=?",(cid,)).fetchone()[0]
                if count==0:
                    c.execute("update conversations set title=?,updated_at=? where id=?",(content.replace("\n"," ")[:34],now,cid))
                else:
                    c.execute("update conversations set updated_at=? where id=?",(now,cid))
            else:
                c.execute("update conversations set updated_at=? where id=?",(now,cid))
            c.execute("insert into messages values(?,?,?,?,?,?)",(mid,cid,role,content,json.dumps(payload,ensure_ascii=False),now))
        return {"id":mid,"conversation_id":cid,"role":role,"content":content,"payload":payload,"created_at":now}
    def save_run(self, run_id: str, conversation_id: str, goal: str, status: str, snapshot: dict[str, Any]) -> None:
        now = float(snapshot.get("updated_at") or time.time())
        created = float(snapshot.get("created_at") or now)
        with self._lock, self._connect() as c:
            c.execute(
                """
                insert into runs(run_id,conversation_id,goal,status,snapshot,created_at,updated_at)
                values(?,?,?,?,?,?,?)
                on conflict(run_id) do update set
                  conversation_id=excluded.conversation_id,goal=excluded.goal,status=excluded.status,
                  snapshot=excluded.snapshot,updated_at=excluded.updated_at
                """,
                (run_id, conversation_id, goal, status, json.dumps(snapshot, ensure_ascii=False), created, now),
            )

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as c:
            row = c.execute("select snapshot from runs where run_id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return json.loads(row["snapshot"])

    def recoverable_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "select run_id,conversation_id,goal,status,snapshot from runs where status in ('running','interrupted','cancel_requested') order by updated_at desc limit ?",
                (limit,),
            ).fetchall()
        out = []
        for row in rows:
            snapshot = json.loads(row["snapshot"] or "{}")
            out.append({
                "run_id": row["run_id"],
                "conversation_id": row["conversation_id"],
                "goal": row["goal"],
                "status": row["status"],
                "snapshot": snapshot,
            })
        return out

    def assistant_for_job(self, conversation_id: str, job_id: str) -> dict[str, Any] | None:
        for message in reversed(self.list_messages(conversation_id)):
            if message["role"] == "assistant" and message.get("payload", {}).get("job_id") == job_id:
                return message
        return None


    def referenced_attachment_ids(self) -> set[str]:
        def collect(value, out: set[str]) -> None:
            if isinstance(value, dict):
                attachment_id = value.get("id")
                if isinstance(attachment_id, str) and attachment_id.startswith("att-"):
                    out.add(attachment_id)
                for child in value.values():
                    collect(child, out)
            elif isinstance(value, list):
                for child in value:
                    collect(child, out)
            elif isinstance(value, str) and value.startswith("att-"):
                out.add(value)

        with self._connect() as c:
            message_rows = c.execute("select payload from messages where payload like '%att-%'").fetchall()
            run_rows = c.execute("select snapshot from runs where snapshot like '%att-%'").fetchall()
        referenced: set[str] = set()
        for row in message_rows:
            try:
                collect(json.loads(row["payload"] or "{}"), referenced)
            except (json.JSONDecodeError, TypeError):
                continue
        for row in run_rows:
            try:
                collect(json.loads(row["snapshot"] or "{}"), referenced)
            except (json.JSONDecodeError, TypeError):
                continue
        return referenced
