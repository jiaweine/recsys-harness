"""Durable Context -> Mechanism -> Experiment -> Evidence -> Outcome graph.

Strategy-arm credit stays deliberately compact because it sits on the hot routing
path. This module is the explanatory memory plane: it preserves *why* a typed
mechanism was accepted, rejected, or inconclusive under a concrete evidence
context. Writes happen only after completed evolution actions and never modify
trust thresholds, active strategies, rollback state, or activation authority.

The dependency-light path stores SQLite evidence and exports JSON-LD. Projects that
install the ``ontology`` extra can additionally validate the exported graph against
the packaged OWL/RDFS vocabulary and SHACL shapes.
"""

from __future__ import annotations

from hashlib import blake2b
from importlib import resources
import json
import time
from typing import Any


MECHANISM_NS = "https://xushu.ai/ontology/mechanism#"
MECHANISM_METHOD = "context_mechanism_experiment_evidence_outcome"


def _stable_key(payload: Any, *, size: int = 16) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )
    return blake2b(raw.encode("utf-8"), digest_size=size).hexdigest()


def _memory_primitives(memory: Any) -> tuple[Any, Any, Any]:
    return (
        getattr(memory, "_lock"),
        getattr(memory, "_connect"),
        getattr(memory, "_close"),
    )


def _surface(tool: str) -> str:
    text = str(tool or "")
    if text.startswith("search."):
        return "search"
    if text.startswith("recommend."):
        return "recommend"
    return ""


def _selected_signature(result: dict[str, Any]) -> list[str]:
    return [
        str(value)
        for value in ((result.get("evolution") or {}).get("selected_signature") or [])
        if str(value) and str(value) != "local:neutral"
    ]


def _global_evidence(result: dict[str, Any]) -> dict[str, Any]:
    business = result.get("business_validation") or {}
    holdout = (result.get("validation") or {}).get("holdout") or {}
    confidence = business.get("confidence") or {}
    return {
        "evaluation_basis": result.get("evaluation_basis"),
        "business_available": bool(business.get("available")),
        "business_holdout_delta": business.get("holdout_reward_delta"),
        "business_full_delta": business.get("full_reward_delta"),
        "confidence_samples": int(confidence.get("samples", 0) or 0),
        "probability_positive": confidence.get("probability_positive"),
        "holdout_independent": bool(holdout.get("independent")),
        "holdout_samples": int(holdout.get("samples", 0) or 0),
        "objective_delta": result.get("objective_delta"),
        "robustness": result.get("robustness"),
        "trust_blocked_by": list(result.get("trust_blocked_by") or []),
    }


def _global_outcome(result: dict[str, Any]) -> tuple[str, float, int, list[str]]:
    business = result.get("business_validation") or {}
    holdout = (result.get("validation") or {}).get("holdout") or {}
    blockers = [str(value) for value in result.get("trust_blocked_by") or []]
    if result.get("trusted"):
        outcome = "accepted"
    elif result.get("evaluation_ready") and (
        not result.get("safe_to_try")
        or float(business.get("holdout_reward_delta", 0.0) or 0.0) < 0.0
        or blockers
    ):
        outcome = "rejected"
    else:
        outcome = "inconclusive"

    if business.get("available"):
        delta = float(business.get("holdout_reward_delta", 0.0) or 0.0)
        evidence = int((business.get("confidence") or {}).get("samples", 0) or 0)
    else:
        delta = float(result.get("objective_delta", 0.0) or 0.0)
        evidence = int(holdout.get("samples", 0) or 0)
    if outcome == "rejected" and not blockers:
        blockers = ["holdout_or_guardrail_rejected"]
    return outcome, delta, evidence, blockers


def _segment_outcome(entry: dict[str, Any]) -> tuple[str, float, int, list[str]]:
    blockers = [str(value) for value in entry.get("trust_blocked_by") or []]
    if entry.get("trusted"):
        outcome = "accepted"
    elif (
        not entry.get("safe_to_try")
        or float(entry.get("holdout_reward_delta", 0.0) or 0.0) < 0.0
    ):
        outcome = "rejected"
    else:
        outcome = "inconclusive"
    evidence = int(entry.get("holdout_requests", 0) or 0)
    delta = float(entry.get("holdout_reward_delta", 0.0) or 0.0)
    if outcome == "rejected" and not blockers:
        blockers = ["segment_holdout_or_guardrail_rejected"]
    return outcome, delta, evidence, blockers


def _context_payload(
    result: dict[str, Any],
    *,
    surface: str,
    scope: str,
    segment: str = "",
) -> dict[str, Any]:
    evolution = result.get("evolution") or {}
    return {
        "surface": surface,
        "scope": scope,
        "segment": segment or None,
        "evaluation_basis": result.get("evaluation_basis"),
        "optimizer_backend": evolution.get("optimizer_backend") or "native",
        "optimizer_method": evolution.get("method"),
        "data": result.get("_run_data_summary") or {},
    }


def _segment_signature(
    memory: Any,
    surface: str,
    global_config: dict[str, Any] | None,
    candidate_config: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(global_config, dict) or not isinstance(candidate_config, dict):
        return []
    helper = getattr(memory, "_config_signature", None)
    if not callable(helper):
        return []
    try:
        return [
            str(value)
            for value in helper(surface, global_config, candidate_config)
            if str(value) and str(value) != "local:neutral"
        ]
    except (TypeError, ValueError, KeyError):
        return []


def _segment_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "discovery_requests": int(entry.get("discovery_requests", 0) or 0),
        "holdout_requests": int(entry.get("holdout_requests", 0) or 0),
        "holdout_reward_delta": entry.get("holdout_reward_delta"),
        "full_reward_delta": entry.get("full_reward_delta"),
        "confidence": entry.get("confidence") or {},
        "guardrail": entry.get("guardrail") or {},
        "trust_blocked_by": list(entry.get("trust_blocked_by") or []),
    }


def _ensure_schema(conn: Any) -> None:
    conn.executescript(
        """
        create table if not exists agent_mechanism_evidence(
          event_key text primary key,
          catalog_key text not null,
          run_id text not null,
          invocation_id text not null,
          domain text not null,
          scope text not null,
          segment text not null,
          context_key text not null,
          mechanism_key text not null,
          outcome text not null,
          reward_delta real not null,
          evidence integer not null,
          failure_modes text not null,
          context_payload text not null,
          evidence_payload text not null,
          created_at real not null
        );
        create index if not exists idx_agent_mechanism_evidence_lookup
          on agent_mechanism_evidence(catalog_key,domain,created_at desc);

        create table if not exists agent_mechanism_stats(
          catalog_key text not null,
          domain text not null,
          scope text not null,
          segment text not null,
          context_key text not null,
          mechanism_key text not null,
          accepted integer not null default 0,
          rejected integer not null default 0,
          inconclusive integer not null default 0,
          trials integer not null default 0,
          reward_sum real not null default 0,
          max_evidence integer not null default 0,
          last_outcome text not null,
          last_failure_modes text not null,
          updated_at real not null,
          primary key(catalog_key,domain,scope,segment,context_key,mechanism_key)
        );
        """
    )


def _insert_event(
    conn: Any,
    *,
    event_key: str,
    catalog_key: str,
    run_id: str,
    invocation_id: str,
    domain: str,
    scope: str,
    segment: str,
    context_key: str,
    mechanism: str,
    outcome: str,
    reward_delta: float,
    evidence: int,
    failure_modes: list[str],
    context: dict[str, Any],
    evidence_payload: dict[str, Any],
) -> bool:
    now = time.time()
    failures_json = json.dumps(sorted(set(failure_modes)), ensure_ascii=False)
    cursor = conn.execute(
        """
        insert or ignore into agent_mechanism_evidence(
          event_key,catalog_key,run_id,invocation_id,domain,scope,segment,
          context_key,mechanism_key,outcome,reward_delta,evidence,failure_modes,
          context_payload,evidence_payload,created_at
        ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event_key,
            catalog_key,
            run_id,
            invocation_id,
            domain,
            scope,
            segment,
            context_key,
            mechanism,
            outcome,
            float(reward_delta),
            max(0, int(evidence)),
            failures_json,
            json.dumps(context, ensure_ascii=False, sort_keys=True, default=str),
            json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, default=str),
            now,
        ),
    )
    if cursor.rowcount == 0:
        return False

    accepted = int(outcome == "accepted")
    rejected = int(outcome == "rejected")
    inconclusive = int(outcome == "inconclusive")
    conn.execute(
        """
        insert into agent_mechanism_stats(
          catalog_key,domain,scope,segment,context_key,mechanism_key,
          accepted,rejected,inconclusive,trials,reward_sum,max_evidence,
          last_outcome,last_failure_modes,updated_at
        ) values(?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)
        on conflict(catalog_key,domain,scope,segment,context_key,mechanism_key)
        do update set
          accepted=accepted+excluded.accepted,
          rejected=rejected+excluded.rejected,
          inconclusive=inconclusive+excluded.inconclusive,
          trials=trials+1,
          reward_sum=reward_sum+excluded.reward_sum,
          max_evidence=max(max_evidence,excluded.max_evidence),
          last_outcome=excluded.last_outcome,
          last_failure_modes=excluded.last_failure_modes,
          updated_at=excluded.updated_at
        """,
        (
            catalog_key,
            domain,
            scope,
            segment,
            context_key,
            mechanism,
            accepted,
            rejected,
            inconclusive,
            float(reward_delta),
            max(0, int(evidence)),
            outcome,
            failures_json,
            now,
        ),
    )
    return True


def _record_mechanisms(
    conn: Any,
    *,
    catalog_key: str,
    run_id: str,
    invocation_id: str,
    domain: str,
    scope: str,
    segment: str,
    context: dict[str, Any],
    mechanisms: list[str],
    outcome: str,
    reward_delta: float,
    evidence: int,
    failure_modes: list[str],
    evidence_payload: dict[str, Any],
) -> tuple[int, int]:
    context_key = _stable_key(context, size=12)
    inserted = 0
    duplicate = 0
    for mechanism in mechanisms:
        event_key = _stable_key(
            {
                "run_id": run_id,
                "invocation_id": invocation_id,
                "scope": scope,
                "segment": segment,
                "context_key": context_key,
                "mechanism": mechanism,
            }
        )
        created = _insert_event(
            conn,
            event_key=event_key,
            catalog_key=catalog_key,
            run_id=run_id,
            invocation_id=invocation_id,
            domain=domain,
            scope=scope,
            segment=segment,
            context_key=context_key,
            mechanism=mechanism,
            outcome=outcome,
            reward_delta=reward_delta,
            evidence=evidence,
            failure_modes=failure_modes,
            context=context,
            evidence_payload=evidence_payload,
        )
        inserted += int(created)
        duplicate += int(not created)
    return inserted, duplicate


def record_mechanism_evidence(
    memory: Any,
    catalog_key: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Persist global and segment-local mechanism evidence idempotently."""

    run_id = str(result.get("run_id") or "")
    if not run_id:
        return {
            "recorded": 0,
            "deduplicated": 0,
            "mechanisms": 0,
            "contexts": 0,
            "method": MECHANISM_METHOD,
            "reason": "missing_run_id",
        }

    actions = [
        row
        for row in result.get("actions") or []
        if isinstance(row, dict)
        and row.get("status") == "completed"
        and str(row.get("tool") or "").endswith(".evolve")
        and isinstance(row.get("result"), dict)
    ]
    if not actions:
        return {
            "recorded": 0,
            "deduplicated": 0,
            "mechanisms": 0,
            "contexts": 0,
            "method": MECHANISM_METHOD,
            "reason": "no_evolution_actions",
        }

    lock, connect, close = _memory_primitives(memory)
    recorded = 0
    deduplicated = 0
    mechanism_names: set[str] = set()
    context_keys: set[str] = set()

    with lock:
        conn = connect()
        try:
            _ensure_schema(conn)
            for action in actions:
                tool = str(action.get("tool") or "")
                domain = _surface(tool)
                if not domain:
                    continue
                invocation_id = str(action.get("invocation_id") or tool)
                evolution_result = dict(action.get("result") or {})
                evolution_result["_run_data_summary"] = result.get("data") or {}

                global_signature = _selected_signature(evolution_result)
                if global_signature:
                    outcome, delta, evidence, failures = _global_outcome(evolution_result)
                    context = _context_payload(
                        evolution_result,
                        surface=domain,
                        scope="global",
                    )
                    context_keys.add(_stable_key(context, size=12))
                    mechanism_names.update(global_signature)
                    created, duplicate = _record_mechanisms(
                        conn,
                        catalog_key=str(catalog_key),
                        run_id=run_id,
                        invocation_id=invocation_id,
                        domain=domain,
                        scope="global",
                        segment="",
                        context=context,
                        mechanisms=global_signature,
                        outcome=outcome,
                        reward_delta=delta,
                        evidence=evidence,
                        failure_modes=failures,
                        evidence_payload=_global_evidence(evolution_result),
                    )
                    recorded += created
                    deduplicated += duplicate

                global_config = evolution_result.get("candidate_config")
                portfolio = evolution_result.get("segment_portfolio") or {}
                for entry in portfolio.get("entries") or []:
                    if not isinstance(entry, dict):
                        continue
                    segment = str(entry.get("segment") or "")
                    if not segment:
                        continue
                    signature = _segment_signature(
                        memory,
                        domain,
                        global_config if isinstance(global_config, dict) else None,
                        entry.get("candidate_config")
                        if isinstance(entry.get("candidate_config"), dict)
                        else None,
                    )
                    if not signature:
                        continue
                    outcome, delta, evidence, failures = _segment_outcome(entry)
                    context = _context_payload(
                        evolution_result,
                        surface=domain,
                        scope="segment",
                        segment=segment,
                    )
                    context_keys.add(_stable_key(context, size=12))
                    mechanism_names.update(signature)
                    created, duplicate = _record_mechanisms(
                        conn,
                        catalog_key=str(catalog_key),
                        run_id=run_id,
                        invocation_id=invocation_id,
                        domain=domain,
                        scope="segment",
                        segment=segment,
                        context=context,
                        mechanisms=signature,
                        outcome=outcome,
                        reward_delta=delta,
                        evidence=evidence,
                        failure_modes=failures,
                        evidence_payload=_segment_evidence(entry),
                    )
                    recorded += created
                    deduplicated += duplicate
            conn.commit()
        finally:
            close(conn)

    return {
        "recorded": recorded,
        "deduplicated": deduplicated,
        "mechanisms": len(mechanism_names),
        "contexts": len(context_keys),
        "method": MECHANISM_METHOD,
    }


def mechanism_stats(
    memory: Any,
    catalog_key: str,
    *,
    domain: str | None = None,
    limit: int = 128,
) -> list[dict[str, Any]]:
    lock, connect, close = _memory_primitives(memory)
    bounded = max(1, min(512, int(limit)))
    with lock:
        conn = connect()
        try:
            _ensure_schema(conn)
            if domain:
                rows = conn.execute(
                    """
                    select * from agent_mechanism_stats
                    where catalog_key=? and domain=?
                    order by trials desc, updated_at desc limit ?
                    """,
                    (str(catalog_key), str(domain), bounded),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    select * from agent_mechanism_stats
                    where catalog_key=?
                    order by trials desc, updated_at desc limit ?
                    """,
                    (str(catalog_key), bounded),
                ).fetchall()
        finally:
            close(conn)
    return [dict(row) for row in rows]


def _mechanism_rows(
    memory: Any,
    catalog_key: str,
    *,
    domain: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    lock, connect, close = _memory_primitives(memory)
    bounded = max(1, min(512, int(limit)))
    with lock:
        conn = connect()
        try:
            _ensure_schema(conn)
            if domain:
                rows = conn.execute(
                    """
                    select * from agent_mechanism_evidence
                    where catalog_key=? and domain=?
                    order by created_at desc limit ?
                    """,
                    (str(catalog_key), str(domain), bounded),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    select * from agent_mechanism_evidence
                    where catalog_key=?
                    order by created_at desc limit ?
                    """,
                    (str(catalog_key), bounded),
                ).fetchall()
        finally:
            close(conn)
    return [dict(row) for row in rows]


def mechanism_graph_snapshot(
    memory: Any,
    catalog_key: str,
    *,
    domain: str | None = None,
    limit: int = 96,
) -> dict[str, Any]:
    """Export recent durable mechanism evidence as a closed-world JSON-LD graph."""

    rows = _mechanism_rows(memory, catalog_key, domain=domain, limit=limit)
    graph: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_key = str(row["event_key"])
        context_key = str(row["context_key"])
        mechanism_key = str(row["mechanism_key"])
        context_id = f"context:{context_key}"
        mechanism_id = f"mechanism:{_stable_key(mechanism_key, size=10)}"
        experiment_id = f"experiment:{event_key}"
        evidence_id = f"evidence:{event_key}"
        outcome_id = f"outcome:{event_key}"
        context_payload = json.loads(row["context_payload"] or "{}")
        evidence_payload = json.loads(row["evidence_payload"] or "{}")
        failures = json.loads(row["failure_modes"] or "[]")

        graph.setdefault(
            context_id,
            {
                "@id": f"{MECHANISM_NS}{context_id}",
                "@type": "xushu:Context",
                "xushu:contextKey": context_key,
                **context_payload,
            },
        )
        graph.setdefault(
            mechanism_id,
            {
                "@id": f"{MECHANISM_NS}{mechanism_id}",
                "@type": "xushu:Mechanism",
                "xushu:mechanismKey": mechanism_key,
            },
        )
        graph[experiment_id] = {
            "@id": f"{MECHANISM_NS}{experiment_id}",
            "@type": "xushu:Experiment",
            "xushu:runId": row["run_id"],
            "xushu:invocationId": row["invocation_id"],
            "xushu:domain": row["domain"],
            "xushu:scope": row["scope"],
            "xushu:segment": row["segment"],
            "xushu:underContext": {"@id": f"{MECHANISM_NS}{context_id}"},
            "xushu:testsMechanism": {"@id": f"{MECHANISM_NS}{mechanism_id}"},
            "xushu:supportedBy": {"@id": f"{MECHANISM_NS}{evidence_id}"},
            "xushu:hasOutcome": {"@id": f"{MECHANISM_NS}{outcome_id}"},
        }
        graph[evidence_id] = {
            "@id": f"{MECHANISM_NS}{evidence_id}",
            "@type": "xushu:Evidence",
            "xushu:evidenceSamples": int(row["evidence"]),
            "xushu:rewardDelta": float(row["reward_delta"]),
            "xushu:evidencePayload": json.dumps(
                evidence_payload,
                ensure_ascii=False,
                sort_keys=True,
            ),
        }

        failure_refs: list[dict[str, str]] = []
        for failure in failures:
            failure_id = f"failure:{_stable_key(str(failure), size=10)}"
            graph.setdefault(
                failure_id,
                {
                    "@id": f"{MECHANISM_NS}{failure_id}",
                    "@type": "xushu:FailureMode",
                    "xushu:failureKey": str(failure),
                },
            )
            failure_refs.append({"@id": f"{MECHANISM_NS}{failure_id}"})
        outcome_node: dict[str, Any] = {
            "@id": f"{MECHANISM_NS}{outcome_id}",
            "@type": "xushu:Outcome",
            "xushu:outcomeStatus": row["outcome"],
        }
        if failure_refs:
            outcome_node["xushu:hasFailureMode"] = failure_refs
        graph[outcome_id] = outcome_node

    jsonld = {
        "@context": {"xushu": MECHANISM_NS, "id": "@id", "type": "@type"},
        "@graph": [graph[key] for key in sorted(graph)],
    }
    violations = validate_mechanism_graph(jsonld)
    return {
        "valid": not violations,
        "violations": violations,
        "events": len(rows),
        "jsonld": jsonld,
        "fingerprint": _stable_key(jsonld),
    }


def validate_mechanism_graph(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Fast closed-world validation for experiment relation targets."""

    jsonld = snapshot.get("jsonld") if "jsonld" in snapshot else snapshot
    rows = jsonld.get("@graph") if isinstance(jsonld, dict) else None
    if not isinstance(rows, list):
        return [
            {
                "shape": "MechanismGraphShape",
                "focus": "graph",
                "message": "@graph must be a list",
            }
        ]
    ids = {str(row.get("@id")) for row in rows if isinstance(row, dict)}
    violations: list[dict[str, str]] = []
    required = (
        "xushu:underContext",
        "xushu:testsMechanism",
        "xushu:supportedBy",
        "xushu:hasOutcome",
    )
    for row in rows:
        if not isinstance(row, dict) or row.get("@type") != "xushu:Experiment":
            continue
        focus = str(row.get("@id") or "experiment")
        for relation in required:
            target = row.get(relation)
            if not isinstance(target, dict) or not target.get("@id"):
                violations.append(
                    {
                        "shape": "ExperimentEvidenceShape",
                        "focus": focus,
                        "message": f"missing {relation}",
                    }
                )
                continue
            if str(target["@id"]) not in ids:
                violations.append(
                    {
                        "shape": "ClosedWorldReferenceShape",
                        "focus": focus,
                        "message": f"unknown target for {relation}",
                    }
                )
    return violations


def validate_mechanism_graph_with_shacl(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Run optional RDF/SHACL validation against packaged mechanism shapes."""

    try:
        import rdflib
        from pyshacl import validate
    except ImportError as exc:  # pragma: no cover - exercised by ontology extra CI
        raise RuntimeError(
            "mechanism SHACL validation requires the ontology extra; "
            "install with `pip install -e '.[ontology]'`"
        ) from exc

    jsonld = snapshot.get("jsonld") if "jsonld" in snapshot else snapshot
    data_graph = rdflib.Graph()
    data_graph.parse(data=json.dumps(jsonld, ensure_ascii=False), format="json-ld")

    ontology_root = resources.files("lingjing_harness.ontology")
    ontology_graph = rdflib.Graph()
    ontology_graph.parse(
        str(ontology_root.joinpath("xushu-mechanism.ttl")),
        format="turtle",
    )
    shapes_graph = rdflib.Graph()
    shapes_graph.parse(
        str(ontology_root.joinpath("xushu-mechanism-shapes.ttl")),
        format="turtle",
    )
    conforms, results_graph, results_text = validate(
        data_graph,
        shacl_graph=shapes_graph,
        ont_graph=ontology_graph,
        inference="rdfs",
        abort_on_first=False,
        meta_shacl=True,
        advanced=True,
    )
    return {
        "conforms": bool(conforms),
        "results_text": str(results_text),
        "result_triples": len(results_graph),
    }


__all__ = [
    "MECHANISM_NS",
    "MECHANISM_METHOD",
    "record_mechanism_evidence",
    "mechanism_stats",
    "mechanism_graph_snapshot",
    "validate_mechanism_graph",
    "validate_mechanism_graph_with_shacl",
]
