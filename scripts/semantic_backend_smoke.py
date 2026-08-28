from __future__ import annotations

import json

from lingjing_harness.adapters import AdapterSearchEngine
from lingjing_harness.algorithms import audit_search
from lingjing_harness.integrations import FlagEmbeddingSearchAdapter
from lingjing_harness.sample_data import build_sample_catalog


def main() -> None:
    catalog = build_sample_catalog()
    adapter = FlagEmbeddingSearchAdapter(
        catalog,
        model_kwargs={"devices": "cpu"},
    )
    engine = AdapterSearchEngine(adapter)
    audit = audit_search(catalog, engine)

    payload = {
        "backend": adapter.capability_manifest(),
        "quality": audit.get("quality"),
        "recall": audit.get("recall"),
        "mrr": audit.get("mrr"),
        "details": audit.get("details"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if float(audit.get("recall") or 0.0) < 0.95:
        raise SystemExit("FlagEmbedding sample recall fell below 0.95")
    if float(audit.get("quality") or 0.0) < 0.90:
        raise SystemExit("FlagEmbedding sample NDCG fell below 0.90")


if __name__ == "__main__":
    main()
