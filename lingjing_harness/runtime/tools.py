from __future__ import annotations

from typing import Any

from lingjing_harness.algorithms import SearchEngine, RecommendationEngine, audit_search, audit_recommend, compare_search, compare_recommend
from lingjing_harness.domain import Catalog
from .contracts import ToolSpec


class ToolRegistry:
    def __init__(self,catalog:Catalog)->None:
        self.catalog=catalog; self.search=SearchEngine(catalog); self.recommend=RecommendationEngine(catalog)
        self._specs={
            "data.inspect":ToolSpec("data.inspect","Inspect catalog and feedback readiness","read",self.inspect_data),
            "search.run":ToolSpec("search.run","Run the current search experience","read",self.run_search),
            "search.audit":ToolSpec("search.audit","Evaluate search on labeled queries","simulation",self.search_audit),
            "search.compare":ToolSpec("search.compare","Compare a shadow search configuration","simulation",self.search_compare),
            "recommend.run":ToolSpec("recommend.run","Generate a recommendation slate","read",self.run_recommend),
            "recommend.audit":ToolSpec("recommend.audit","Evaluate recommendation coverage and freshness","simulation",self.recommend_audit),
            "recommend.compare":ToolSpec("recommend.compare","Compare a shadow recommendation configuration","simulation",self.recommend_compare),
        }

    def replace_catalog(self,catalog:Catalog)->None:
        self.__init__(catalog)

    def get(self,name:str)->ToolSpec:
        if name not in self._specs: raise KeyError(f"unknown tool: {name}")
        return self._specs[name]

    def inspect_data(self)->dict[str,Any]:
        s=self.catalog.summary(); issues=[]
        if s["interactions"]==0: issues.append("缺少用户行为记录，个性化结果会更多依赖内容本身")
        if s["queries"]==0: issues.append("缺少人工复核查询，搜索只能做结构性检查")
        if s["items"]<12: issues.append("内容规模较小，离线结论的稳定性有限")
        dup=len(self.catalog.items)-len({x.title.strip().lower() for x in self.catalog.items})
        if dup: issues.append(f"发现 {dup} 条重复标题")
        unavailable=sum(1 for x in self.catalog.items if not x.eligible)
        if unavailable: issues.append(f"有 {unavailable} 条内容当前不可展示")
        return {"summary":s,"issues":issues}

    def run_search(self,query:str|None=None,**_:Any)->dict[str,Any]: return {"query":query or "", "results":self.search.search(query or "",limit=8)}
    def search_audit(self,**_:Any)->dict[str,Any]: return audit_search(self.catalog,self.search)
    def search_compare(self,**_:Any)->dict[str,Any]: return compare_search(self.catalog,self.search)
    def run_recommend(self,user_id:str|None=None,**_:Any)->dict[str,Any]: return {"user_id":user_id or "new-user","results":self.recommend.recommend(user_id or "new-user",limit=8)}
    def recommend_audit(self,**_:Any)->dict[str,Any]: return audit_recommend(self.catalog,self.recommend)
    def recommend_compare(self,**_:Any)->dict[str,Any]: return compare_recommend(self.catalog,self.recommend)
