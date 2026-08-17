from __future__ import annotations

from dataclasses import dataclass,field
import time,uuid
from typing import Any,Callable

from lingjing_harness.domain import Catalog
from .policy import OwnedPolicy
from .tools import ToolRegistry
from .verifier import ResultVerifier

EventSink=Callable[[dict[str,Any]],None]


@dataclass(slots=True)
class RunEvent:
    phase:str; title:str; detail:str; progress:int; payload:dict[str,Any]=field(default_factory=dict); created_at:float=field(default_factory=time.time)
    def dict(self)->dict[str,Any]: return {"phase":self.phase,"title":self.title,"detail":self.detail,"progress":self.progress,"payload":self.payload,"created_at":self.created_at}


class AgentHarness:
    """Vertical search/recommendation agent harness with owned planning and execution algorithms."""

    def __init__(self,catalog:Catalog,max_tools:int=10)->None:
        self.catalog=catalog; self.policy=OwnedPolicy(); self.tools=ToolRegistry(catalog); self.verifier=ResultVerifier(); self.max_tools=max_tools

    def replace_catalog(self,catalog:Catalog)->None:
        self.catalog=catalog; self.tools.replace_catalog(catalog)

    @staticmethod
    def _emit(events:list[RunEvent],sink:EventSink|None,phase:str,title:str,detail:str,progress:int,**payload:Any)->None:
        ev=RunEvent(phase,title,detail,progress,payload); events.append(ev)
        if sink: sink(ev.dict())

    def run(self,text:str,*,sink:EventSink|None=None)->dict[str,Any]:
        run_id=f"run-{uuid.uuid4().hex[:10]}"; events=[]; findings=[]; evidence=[]; actions=[]; blocks=[]
        self._emit(events,sink,"observe","读取当前工作区","确认内容规模、用户反馈和可复核样本",6)
        plan=self.policy.plan(text,self.catalog)
        self._emit(events,sink,"plan","拆解目标",self._plan_copy(plan.mode,plan.compare),14,mode=plan.mode,steps=len(plan.steps))
        for i,step in enumerate(plan.steps[:self.max_tools]):
            progress=20+int(65*(i+1)/max(1,len(plan.steps)))
            spec=self.tools.get(step.tool)
            self._emit(events,sink,"execute",step.title,step.detail,progress,tool=spec.name,risk=spec.risk)
            result=spec.handler(**step.args); actions.append({"tool":spec.name,"risk":spec.risk,"input":step.args,"result":result})
            if step.tool=="data.inspect": findings.extend(result.get("issues",[])); continue
            if step.tool=="search.run":
                rows=result["results"]; findings.extend(self.verifier.search(rows)); evidence.extend({"kind":"result","title":x["title"],"detail":f"当前第 {x['rank']} 位","score":x["score"]} for x in rows[:4])
                blocks.append(f"搜索“{result['query']}”当前最靠前的是："+"、".join(x["title"] for x in rows[:3])+"。" if rows else f"搜索“{result['query']}”当前没有可展示结果。")
            elif step.tool=="search.audit":
                if result.get("queries"):
                    if result.get("sampled"):
                        blocks.append(f"我从 {result.get('available_queries', result['queries'])} 个已知查询中抽样 {result['queries']} 个做了复核，整体命中质量约为 {result['quality']:.0%}。")
                    else:
                        blocks.append(f"我又用 {result['queries']} 个已知查询做了复核，整体命中质量约为 {result['quality']:.0%}。")
                else: blocks.append("当前没有人工复核查询，因此只能做结构性检查，建议补一小批真实查询样本。")
            elif step.tool=="search.compare":
                findings.extend(self.verifier.experiment(result)); d=result["delta"].get("quality",0.0)
                if not result.get("evaluation_ready", True):
                    blocks.append("当前缺少可复核查询，候选搜索方案不能进入流量验证。")
                else:
                    blocks.append("候选方案离线表现更好，可以进入小流量验证。" if result.get("safe_to_try") and d>0 else "候选方案没有形成稳定优势，先不建议扩大流量。")
            elif step.tool=="recommend.run":
                rows=result["results"]; findings.extend(self.verifier.recommend(rows)); evidence.extend({"kind":"result","title":x["title"],"detail":f"当前第 {x['rank']} 位","score":x["score"]} for x in rows[:4])
                blocks.append(f"用户 {result['user_id']} 当前最靠前的是："+"、".join(x["title"] for x in rows[:3])+"。" if rows else f"用户 {result['user_id']} 当前没有可展示内容。")
            elif step.tool=="recommend.audit":
                prefix = f"抽样检查 {result.get('users',0)}/{result.get('available_users',result.get('users',0))} 个用户后，" if result.get("sampled") else ""
                blocks.append(prefix+f"当前内容覆盖约 {result.get('coverage',0):.0%}，新鲜度约 {result.get('freshness',0):.0%}，结果分散度约 {result.get('diversity',0):.0%}。")
            elif step.tool=="recommend.compare":
                findings.extend(self.verifier.experiment(result)); d=result["delta"]
                if not result.get("evaluation_ready", True):
                    blocks.append("当前缺少可复核用户行为，候选推荐方案不能进入流量验证。")
                elif result.get("safe_to_try"):
                    blocks.append(f"候选方案通过离线门槛，新鲜度变化 {d.get('freshness',0):+.1%}，覆盖变化 {d.get('coverage',0):+.1%}。")
                else:
                    blocks.append("候选推荐方案存在覆盖或整体质量回退，先不建议扩大流量。")
        self._emit(events,sink,"verify","核对结论","只保留能被本次真实执行与当前数据支持的判断",91)
        findings=list(dict.fromkeys(x for x in findings if x)) or ["本次执行未发现阻断性问题"]
        suggestions=self._suggestions(plan.mode,plan.compare,findings)
        self._emit(events,sink,"complete","形成可执行结论","已整理本次发现、证据与下一步动作",100)
        answer=self._answer(blocks,findings,suggestions)
        return {"run_id":run_id,"answer":answer,"plan":{"mode":plan.mode,"query":plan.query,"user_id":plan.user_id,"compare":plan.compare},"events":[x.dict() for x in events],"findings":findings[:8],"evidence":evidence[:10],"suggestions":suggestions,"actions":actions,"data":self.catalog.summary(),"owned_policy":True}

    @staticmethod
    def _plan_copy(mode:str,compare:bool)->str:
        base={"search":"先复现指定搜索，再检查是不是整体问题","recommend":"先复现一屏推荐，再看不同用户是否都有稳定结果","both":"同时检查搜索与推荐两条体验链路","audit":"先做全局体检，再确定最值得深入的方向"}[mode]
        return base+("，最后离线比较一个候选方案" if compare else "")

    @staticmethod
    def _suggestions(mode:str,compare:bool,findings:list[str])->list[str]:
        if mode=="search": rows=["把最差的查询样本展开","检查无结果与低相关查询","生成一份小流量验证清单"]
        elif mode=="recommend": rows=["换一个用户继续看","检查新用户首屏体验","生成一份小流量验证清单"]
        elif mode=="both": rows=["先深入搜索问题","先深入推荐问题","整理成一页上线检查表"]
        else: rows=["先看搜索体验","先看推荐体验","导入我的真实数据"]
        if compare and any("未通过" in x or "不建议" in x for x in findings): rows[2]="继续调整候选方案"
        return rows

    @staticmethod
    def _answer(blocks:list[str],findings:list[str],suggestions:list[str])->str:
        body="\n".join(f"- {x}" for x in findings[:4])
        nexts="\n".join(f"- {x}" for x in suggestions[:3])
        lead=" ".join(blocks) if blocks else "我已经把当前工作区做了一次完整检查。"
        return f"### 结论\n{lead}\n\n### 需要注意\n{body}\n\n### 下一步\n{nexts}"
