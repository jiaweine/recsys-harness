from __future__ import annotations

from hashlib import blake2b
import json

from lingjing_harness.domain import Catalog
from lingjing_harness.runtime.memory import catalog_fingerprint


def workspace_fingerprint(catalog: Catalog) -> str:
    """Fingerprint every datum that changes the executable/evaluation workspace.

    Durable strategy memory intentionally survives appended production events, but
    worker synchronization cannot: a worker with yesterday's outcomes must not
    evaluate or activate against a newer evidence snapshot.  The workspace
    revision therefore extends the stable strategy-context fingerprint with the
    complete canonical production-event snapshot.
    """

    stable = catalog_fingerprint(catalog)
    if not catalog.events:
        return stable
    payload = [event.to_dict() for event in catalog.events]
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = blake2b(digest_size=12)
    digest.update(f"{stable}|production-evidence-v1|{raw}".encode("utf-8"))
    return digest.hexdigest()


__all__ = ["workspace_fingerprint"]
