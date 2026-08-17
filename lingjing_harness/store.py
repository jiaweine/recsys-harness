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
