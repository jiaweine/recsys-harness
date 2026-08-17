from __future__ import annotations

from dataclasses import asdict

from lingjing_harness.domain import Catalog
from .search import SearchConfig, SearchEngine
from .recommend import RecommendConfig, RecommendationEngine
from .evaluation import audit_search, audit_recommend


def compare_search(catalog: Catalog, current: SearchEngine) -> dict:
    baseline=audit_search(catalog,current)
    candidate_cfg=SearchConfig(lexical=.43,semantic=.29,title=.11,quality=.07,popularity=.025,freshness=.075,diversity=.09)
    candidate=SearchEngine(catalog,candidate_cfg); trial=audit_search(catalog,candidate)
    delta=round(trial.get("quality",0.0)-baseline.get("quality",0.0),4)
    safe = delta >= -0.005 and trial.get("recall",0.0) >= baseline.get("recall",0.0)-0.01
    return {"baseline":baseline,"candidate":trial,"delta":{"quality":delta,"recall":round(trial.get("recall",0)-baseline.get("recall",0),4)},"safe_to_try":safe,"candidate_config":asdict(candidate_cfg)}


def compare_recommend(catalog: Catalog, current: RecommendationEngine) -> dict:
    baseline=audit_recommend(catalog,current)
    candidate_cfg=RecommendConfig(profile=.32,graph=.18,category=.09,quality=.13,freshness=.16,popularity=.035,novelty=.075,diversity=.16,exploration=.05)
    candidate=RecommendationEngine(catalog,candidate_cfg); trial=audit_recommend(catalog,candidate)
    q_delta=round(trial.get("quality",0)-baseline.get("quality",0),4); fresh_delta=round(trial.get("freshness",0)-baseline.get("freshness",0),4); cov_delta=round(trial.get("coverage",0)-baseline.get("coverage",0),4)
    safe=q_delta>=-0.005 and cov_delta>=-0.02 and fresh_delta>=0
    return {"baseline":baseline,"candidate":trial,"delta":{"quality":q_delta,"freshness":fresh_delta,"coverage":cov_delta},"safe_to_try":safe,"candidate_config":asdict(candidate_cfg)}
