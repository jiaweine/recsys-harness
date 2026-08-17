from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.algorithms import RecommendationEngine, audit_recommend


def test_recommend_filters_seen_and_has_variety():
    catalog=build_sample_catalog(); engine=RecommendationEngine(catalog)
    seen={e.item_id for e in catalog.interactions if e.user_id=="u-lin"}
    rows=engine.recommend("u-lin",limit=8)
    assert rows
    assert not (seen & {x["id"] for x in rows})
    assert len({c for x in rows for c in x["categories"]}) >= 5


def test_recommend_audit_has_coverage():
    catalog=build_sample_catalog(); report=audit_recommend(catalog,RecommendationEngine(catalog))
    assert report["users"] == 5
    assert report["coverage"] > .5
    assert report["freshness"] > .6
