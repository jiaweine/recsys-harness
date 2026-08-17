from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.algorithms import SearchEngine, audit_search


def test_search_finds_core_intent():
    engine=SearchEngine(build_sample_catalog())
    rows=engine.search("露营灯",limit=5)
    assert rows
    assert rows[0]["id"] in {"p05","p06"}
    assert {x["id"] for x in rows[:3]} >= {"p05","p06"}


def test_search_audit_is_meaningful():
    catalog=build_sample_catalog(); report=audit_search(catalog,SearchEngine(catalog))
    assert report["queries"] >= 5
    assert report["quality"] >= .65
    assert report["recall"] >= .6


def test_search_does_not_use_hash_collision_as_retrieval_evidence():
    engine=SearchEngine(build_sample_catalog())
    assert engine.search("完全不存在的词",limit=5) == []
    rows=engine.search("露营灯",limit=5)
    assert "p20" not in {x["id"] for x in rows}


def test_search_downweights_generic_query_suffixes():
    engine=SearchEngine(build_sample_catalog())
    rows=engine.search("夜跑装备",limit=3)
    assert rows
    assert rows[0]["id"] == "p21"
