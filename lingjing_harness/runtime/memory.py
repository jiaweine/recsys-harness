"""Stable durable-memory surface.

``memory_core`` keeps the SQLite implementation.  The strategy identity here is
stable across appended production outcome rows but changes when the product-owned
RewardSpec changes, so validated experience can survive new evidence without
silently crossing into a different business objective.
"""

from __future__ import annotations

from hashlib import blake2b
import json

from lingjing_harness.domain import Catalog
from .memory_core import AgentMemory, catalog_fingerprint as _catalog_fingerprint


def catalog_fingerprint(catalog: Catalog) -> str:
    """Fingerprint the stable strategy context, not the mutable evidence snapshot.

    Items, interactions and relevance labels retain the historical fingerprint.
    Production events are deliberately excluded because appending fresh outcomes
    should trigger re-evaluation, not erase every learned strategy.  RewardSpec is
    included because changing the business objective invalidates prior credit.
    Workspace synchronization uses a separate full evidence fingerprint.
    """

    base = _catalog_fingerprint(catalog)
    if catalog.reward_spec is None:
        return base
    reward = json.dumps(
        catalog.reward_spec.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = blake2b(digest_size=12)
    digest.update(f"{base}|reward-contract-v1|{reward}".encode("utf-8"))
    return digest.hexdigest()


__all__ = ["AgentMemory", "catalog_fingerprint"]
