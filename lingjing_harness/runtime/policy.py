from __future__ import annotations

import re

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog
from .contracts import AgentPlan, PlanStep


class OwnedPolicy:
    """Project-owned planning policy. Natural-language routing does not require an external LLM."""

    SEARCH_HINTS=("搜","搜索","查询","找不到","关键词","结果不准","无结果","搜索体验")
    REC_HINTS=("推荐","首页","feed","猜你喜欢","分发","曝光","推荐体验","个性化")
    AUDIT_HINTS=("体检","健康","整体","全局","质量","问题")
    COMPARE_HINTS=("优化","提升","改进","对比","方案","实验","上线","候选","试试")

    def plan(self,text:str,catalog:Catalog)->AgentPlan:
        lowered=text.lower(); search=any(k in lowered for k in self.SEARCH_HINTS); rec=any(k in lowered for k in self.REC_HINTS)
        if search and rec: mode="both"
        elif search: mode="search"
        elif rec: mode="recommend"
        else: mode="audit"
        compare=any(k in lowered for k in self.COMPARE_HINTS)
        query=self._extract_query(text,catalog) if mode in {"search","both"} else None
        user=self._extract_user(text,catalog) if mode in {"recommend","both"} else None
        steps=[PlanStep("data.inspect","检查当前数据","确认内容、行为与可复核样本是否足够")]
        if mode in {"search","both"}:
            steps.extend([PlanStep("search.run","复现搜索体验",f"真实运行“{query or '当前查询'}”并保留结果证据",{"query":query}),PlanStep("search.audit","检查整体稳定性","用已知样本复核是否只是单点问题")])
            if compare: steps.append(PlanStep("search.compare","比较改进方案","离线比较一个候选方案，不改线上数据"))
        if mode in {"recommend","both"}:
            steps.extend([PlanStep("recommend.run","复现推荐体验",f"生成用户 {user or '当前用户'} 的一屏结果",{"user_id":user}),PlanStep("recommend.audit","检查整体覆盖","确认不同用户是否都能拿到足够新鲜且不重复的内容")])
            if compare: steps.append(PlanStep("recommend.compare","比较改进方案","离线比较更重视新鲜度与覆盖的候选方案"))
        if mode=="audit": steps.extend([PlanStep("search.audit","检查搜索体验","查看已知查询的整体表现"),PlanStep("recommend.audit","检查推荐体验","查看覆盖、新鲜度与结果分散度")])
        return AgentPlan(mode=mode,goal=text.strip(),query=query,user_id=user,compare=compare,steps=steps)

    @staticmethod
    def _extract_query(text:str,catalog:Catalog)->str:
        quoted=re.findall(r"[‘’'\"“”]([^‘’'\"“”]{1,50})[‘’'\"“”]",text)
        if quoted: return quoted[0].strip()
        for label in catalog.query_labels:
            if label.query and label.query in text: return label.query
        cleaned=re.sub(r"(帮我|请|看下|看看|分析|检查|为什么|搜索|搜一下|搜|不准|不好|优化|改进|结果|体验|一下|最近)"," ",text)
        chunks=[x.strip(" ，。！？,.!?：:") for x in re.split(r"\s+",cleaned) if x.strip()]
        return max(chunks,key=len,default=(catalog.query_labels[0].query if catalog.query_labels else catalog.items[0].title))[:50]

    @staticmethod
    def _extract_user(text:str,catalog:Catalog)->str:
        users=RecommendationEngine(catalog).known_users(); m=re.search(r"(?:用户|user)\s*[:：]?\s*([\w-]+)",text,re.I)
        if m: return m.group(1)
        return users[0] if users else "new-user"
