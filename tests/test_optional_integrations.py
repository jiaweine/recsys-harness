from __future__ import annotations

import pytest

from lingjing_harness.integrations import ImplicitRecommendationAdapter
from lingjing_harness.integrations import implicit_recommendation
from lingjing_harness.sample_data import build_sample_catalog


def test_collaborative_dependency_is_loaded_only_on_adapter_construction(monkeypatch):
    catalog = build_sample_catalog()

    def unavailable():
        raise RuntimeError("collaborative extra required")

    monkeypatch.setattr(implicit_recommendation, "_load_implicit_dependencies", unavailable)

    with pytest.raises(RuntimeError, match="collaborative extra required"):
        ImplicitRecommendationAdapter(catalog)


def test_unknown_model_is_rejected_before_loading_optional_dependency(monkeypatch):
    catalog = build_sample_catalog()
    loaded = False

    def should_not_load():
        nonlocal loaded
        loaded = True
        raise AssertionError("optional dependency loader should not run")

    monkeypatch.setattr(implicit_recommendation, "_load_implicit_dependencies", should_not_load)

    with pytest.raises(ValueError, match="unknown implicit recommendation model"):
        ImplicitRecommendationAdapter(catalog, model="mystery")
    assert loaded is False
