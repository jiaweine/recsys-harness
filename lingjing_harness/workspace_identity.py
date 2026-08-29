from __future__ import annotations

from hashlib import blake2b
import json

from lingjing_harness.domain import Catalog
from lingjing_harness.runtime.memory import catalog_fingerprint


def workspace_fingerprint(catalog: Catalog) -> str:
    """Fingerprint every datum that changes executable/evaluation workspace state.

    Durable strategy memory intentionally survives appended evidence, but worker
    synchronization cannot: workers must converge before evaluating or activating
    against production replay or explicit counterfactual experiment evidence.
    """

    stable = catalog_fingerprint(catalog)
    evidence: dict[str, object] = {}
    if catalog.events:
        evidence["production_events"] = [event.to_dict() for event in catalog.events]
    if catalog.counterfactual_records:
        evidence["counterfactual_records"] = [
            row.to_dict() for row in catalog.counterfactual_records
        ]
    if catalog.experiments:
        evidence["experiments"] = [spec.to_dict() for spec in catalog.experiments]
    if not evidence:
        return stable
    raw = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = blake2b(digest_size=12)
    digest.update(f"{stable}|evaluation-evidence-snapshot|{raw}".encode("utf-8"))
    return digest.hexdigest()


__all__ = ["workspace_fingerprint"]
