from __future__ import annotations

import json

from lingjing_harness.integrations import ImplicitHybridRecommendationEngine
from lingjing_harness.runtime import AgentHarness, RuntimeBackendConfig
from lingjing_harness.sample_data import build_sample_catalog


def main() -> None:
    catalog = build_sample_catalog()
    harness = AgentHarness(
        catalog,
        backend_config=RuntimeBackendConfig(
            recommend_backend="implicit_als",
            recommend_backend_kwargs={"min_history": 1, "collaborative_limit": 12},
        ),
    )
    registry = harness.tools
    engine = registry.recommend
    if not isinstance(engine, ImplicitHybridRecommendationEngine):
        raise SystemExit("public AgentHarness did not install the implicit ALS hybrid runtime")

    candidate_user = next(
        (
            user_id
            for user_id in engine.known_users()
            if engine.adapter.history_count(user_id) >= engine.adapter.min_history
        ),
        None,
    )
    if candidate_user is None:
        raise SystemExit("sample catalog has no collaborative-eligible user")
    rows = registry.run_recommend(candidate_user)["results"]
    forked = harness.fork()
    manifest = registry.inspect_data()["recommend_backend"]
    payload = {
        "backend": manifest,
        "strategy_scope": registry.runtime_backend_config["strategy_scopes"]["recommend"],
        "user_id": candidate_user,
        "result_backends": sorted({str(row.get("backend") or "") for row in rows}),
        "prepared_backend_reused_on_fork": forked.tools.recommend.adapter is engine.adapter,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if manifest.get("model") != "als":
        raise SystemExit("public runtime did not select official implicit ALS")
    if not payload["strategy_scope"].startswith("recommend-"):
        raise SystemExit("implicit ALS runtime did not receive a backend-scoped strategy identity")
    if not any(str(row.get("backend") or "").startswith("hybrid_implicit_als") for row in rows):
        raise SystemExit("implicit ALS signal did not reach the public recommendation slate")
    if not payload["prepared_backend_reused_on_fork"]:
        raise SystemExit("implicit ALS state was retrained instead of reused on fork")


if __name__ == "__main__":
    main()
