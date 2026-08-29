from __future__ import annotations

import json

from lingjing_harness.adapters import AdapterSearchEngine
from lingjing_harness.algorithms import SearchEngine, audit_search
from lingjing_harness.runtime import AgentHarness, RuntimeBackendConfig
from lingjing_harness.sample_data import build_sample_catalog


def main() -> None:
    catalog = build_sample_catalog()
    reference = audit_search(catalog, SearchEngine(catalog))
    harness = AgentHarness(
        catalog,
        backend_config=RuntimeBackendConfig(
            search_backend="flag_embedding",
            search_backend_kwargs={"model_kwargs": {"devices": "cpu"}},
        ),
    )
    registry = harness.tools
    adapter = registry.search.adapter
    dense = audit_search(catalog, AdapterSearchEngine(adapter))
    hybrid = audit_search(catalog, registry.search)
    forked = harness.fork()

    payload = {
        "backend": registry.inspect_data()["search_backend"],
        "strategy_scope": registry.runtime_backend_config["strategy_scopes"]["search"],
        "prepared_backend_reused_on_fork": forked.tools.search.adapter is adapter,
        "reference": {
            "quality": reference.get("quality"),
            "recall": reference.get("recall"),
            "mrr": reference.get("mrr"),
        },
        "dense": {
            "quality": dense.get("quality"),
            "recall": dense.get("recall"),
            "mrr": dense.get("mrr"),
        },
        "hybrid_runtime": {
            "quality": hybrid.get("quality"),
            "recall": hybrid.get("recall"),
            "mrr": hybrid.get("mrr"),
            "details": hybrid.get("details"),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not payload["strategy_scope"].startswith("search-"):
        raise SystemExit("FlagEmbedding runtime did not receive a backend-scoped strategy identity")
    if not payload["prepared_backend_reused_on_fork"]:
        raise SystemExit("FlagEmbedding model state was reloaded instead of reused on fork")
    if float(dense.get("recall") or 0.0) < 0.95:
        raise SystemExit("FlagEmbedding dense sample recall fell below 0.95")
    if float(dense.get("quality") or 0.0) < 0.90:
        raise SystemExit("FlagEmbedding dense sample NDCG fell below 0.90")
    if float(hybrid.get("recall") or 0.0) < 0.95:
        raise SystemExit("FlagEmbedding hybrid runtime sample recall fell below 0.95")
    if float(hybrid.get("quality") or 0.0) + 1e-9 < float(reference.get("quality") or 0.0):
        raise SystemExit("FlagEmbedding hybrid runtime regressed below the reference sample NDCG")


if __name__ == "__main__":
    main()
