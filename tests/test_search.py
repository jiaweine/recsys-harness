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
