from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms.search import (
    SearchEngine,
    _candidate_postings_union,
    _candidate_semantic_rescue,
)
from lingjing_harness.algorithms.text import cosine
from lingjing_harness.domain import Catalog, Item


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "mean_ms": round(statistics.fmean(values), 3) if values else 0.0,
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def _engine(*, items: int, anchors: int) -> tuple[SearchEngine, list[str], dict[int, float]]:
    catalog_items = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Semantic Rescue Item {index}",
            categories=[f"category-{index % 31}"],
            popularity=float((index * 17) % 1000),
            quality=((index * 13) % 100) / 100.0,
            freshness=((index * 29) % 100) / 100.0,
        )
        for index in range(items)
    ]
    vectors = {
        item.item_id: {
            (index * 7) % 256: 0.8,
            (index * 11 + 3) % 256: 0.6,
            (index * 17 + 5) % 256: 0.4,
            (index * 19 + 9) % 256: 0.2,
        }
        for index, item in enumerate(catalog_items)
    }
    engine = SearchEngine(Catalog(items=catalog_items), item_vectors=vectors)
    anchor_ids = [item.item_id for item in catalog_items[:anchors]]
    engine._postings["anchor"] = list(anchor_ids)
    qvec = {7: 0.8, 19: 0.6, 37: 0.4, 83: 0.2}
    return engine, anchor_ids, qvec


def _legacy_semantic_rescue(
    engine: SearchEngine,
    query: str,
    qtokens: list[str],
    retrieval_tokens: list[str],
    qvec: dict[int, float],
) -> dict[str, str]:
    out = _candidate_postings_union(
        engine,
        query,
        qtokens,
        retrieval_tokens,
        qvec,
    )
    if not out:
        return out
    semantic: list[tuple[float, str]] = []
    for item in engine.catalog.items:
        if not item.eligible or item.item_id in out:
            continue
        score = max(0.0, cosine(qvec, engine._vectors[item.item_id]))
        semantic.append((score, item.item_id))
    semantic.sort(key=lambda row: (-row[0], row[1]))
    budget = min(24, max(6, len(out)))
    for score, item_id in semantic[:budget]:
        if score >= 0.16:
            out[item_id] = "semantic"
    return out


def run_benchmark(*, items: int, anchors: int, repeats: int) -> dict[str, object]:
    engine, anchor_ids, qvec = _engine(items=items, anchors=anchors)
    args = ("anchor", ["anchor"], ["anchor"], qvec)

    legacy = _legacy_semantic_rescue(engine, *args)
    current = _candidate_semantic_rescue(engine, *args)
    if current != legacy:
        raise AssertionError("semantic rescue output differs from full-sort reference")
    if not all(legacy.get(item_id) == "lexical" for item_id in anchor_ids):
        raise AssertionError("lexical anchors were not preserved")

    return {
        "items": items,
        "anchors": anchors,
        "budget": min(24, max(6, anchors)),
        "legacy": _summary(
            _timed(
                lambda: _legacy_semantic_rescue(engine, *args),
                repeats,
            )
        ),
        "current": _summary(
            _timed(
                lambda: _candidate_semantic_rescue(engine, *args),
                repeats,
            )
        ),
        "candidate_count": len(current),
        "semantic_count": sum(1 for source in current.values() if source == "semantic"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark search semantic-rescue top-K selection."
    )
    parser.add_argument("--items", type=int, default=50_000)
    parser.add_argument("--anchors", type=int, default=24)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--max-current-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        items=max(1_000, args.items),
        anchors=max(1, min(24, args.anchors)),
        repeats=max(3, args.repeats),
    )
    current_p50 = float(result["current"]["p50_ms"])
    legacy_p50 = float(result["legacy"]["p50_ms"])
    speedup = legacy_p50 / max(current_p50, 1e-9)
    result["speedup_p50"] = round(speedup, 2)
    print(json.dumps(result, sort_keys=True))

    failures: list[str] = []
    if args.max_current_p50_ms > 0 and current_p50 > args.max_current_p50_ms:
        failures.append(
            f"current p50={current_p50} > {args.max_current_p50_ms}"
        )
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(
            f"speedup={speedup:.2f} < {args.min_speedup}"
        )
    if failures:
        raise SystemExit(
            "semantic rescue performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
