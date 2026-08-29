from __future__ import annotations

import argparse
from dataclasses import replace
import json

from .sample_data import build_sample_catalog
from .runtime import AgentHarness, RuntimeBackendConfig


def main() -> None:
    defaults = RuntimeBackendConfig.from_env()
    parser = argparse.ArgumentParser(description="Xushu search/recommendation agent harness")
    parser.add_argument(
        "--search-backend",
        choices=RuntimeBackendConfig.SEARCH_BACKENDS,
        default=None,
        help=f"search backend (env default: {defaults.search_backend})",
    )
    parser.add_argument(
        "--recommend-backend",
        choices=RuntimeBackendConfig.RECOMMEND_BACKENDS,
        default=None,
        help=f"recommendation backend (env default: {defaults.recommend_backend})",
    )
    parser.add_argument(
        "--optimizer-backend",
        choices=RuntimeBackendConfig.OPTIMIZER_BACKENDS,
        default=None,
        help=f"evolution optimizer (env default: {defaults.optimizer_backend})",
    )
    parser.add_argument("prompt", nargs="*", default=["做一次全局体检"])
    args = parser.parse_args()

    backend_config = replace(
        defaults,
        search_backend=args.search_backend or defaults.search_backend,
        recommend_backend=args.recommend_backend or defaults.recommend_backend,
        optimizer_backend=args.optimizer_backend or defaults.optimizer_backend,
    )
    result = AgentHarness(
        build_sample_catalog(),
        backend_config=backend_config,
    ).run(" ".join(args.prompt))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
