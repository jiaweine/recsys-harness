"""Semantic governance graph for mission planning and self-evolution.

The runtime MissionGraph is an execution/evidence DAG.  This module adds a
machine-inspectable semantic layer above it so evidence requirements,
capabilities, risks, authority and hypotheses have stable types and relations.

The default validator is intentionally dependency-free and fast enough to run on
every mission compilation.  An optional RDF/SHACL validator is available through
the ``ontology`` extra for integration and governance audits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import blake2b
from importlib import resources
import json
from typing import Any, Iterable

from .capabilities import CapabilityRegistry
from .contracts import AgentPlan, MissionGraph


ONTOLOGY_NS = "https://xushu.ai/ontology/recsys#"


@dataclass(frozen=True, slots=True)
class SemanticNode:
    node_id: str
    node_type: str
    properties: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "type": self.node_type,
            "properties": dict(self.properties),
        }


@dataclass(frozen=True, slots=True)
class SemanticEdge:
    source: str
    relation: str
    target: str

    def dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "relation": self.relation,
            "target": self.target,
        }


@dataclass(frozen=True, slots=True)
class SemanticViolation:
    shape: str
    focus: str
    message: str
    severity: str = "error"

    def dict(self) -> dict[str, str]:
        return {
            "shape": self.shape,
            "focus": self.focus,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(slots=True)
class SemanticGovernanceGraph:
    """Typed semantic projection of one task-specific mission."""

    nodes: dict[str, SemanticNode] = field(default_factory=dict)
    edges: list[SemanticEdge] = field(default_factory=list)
    violations: list[SemanticViolation] = field(default_factory=list)

    def add_node(self, node: SemanticNode) -> None:
        current = self.nodes.get(node.node_id)
        if current is not None and current != node:
            self.violations.append(
                SemanticViolation(
                    "UniqueIdentityShape",
                    node.node_id,
                    "semantic identity resolves to conflicting node definitions",
                )
            )
            return
        self.nodes[node.node_id] = node

    def add_edge(self, source: str, relation: str, target: str) -> None:
        edge = SemanticEdge(source, relation, target)
        if edge not in self.edges:
            self.edges.append(edge)

    @property
    def valid(self) -> bool:
        return not any(row.severity == "error" for row in self.violations)

    def _validate_edges(self) -> None:
        for edge in self.edges:
            if edge.source not in self.nodes:
                self.violations.append(
                    SemanticViolation(
                        "ClosedWorldReferenceShape",
                        edge.source,
                        f"edge source is not declared: {edge.source}",
                    )
                )
            if edge.target not in self.nodes:
                self.violations.append(
                    SemanticViolation(
                        "ClosedWorldReferenceShape",
                        edge.target,
                        f"edge target is not declared: {edge.target}",
                    )
                )

    def _validate_requirements(self) -> None:
        outgoing: dict[str, list[SemanticEdge]] = {}
        for edge in self.edges:
            outgoing.setdefault(edge.source, []).append(edge)
        for node in self.nodes.values():
            if node.node_type != "EvidenceRequirement":
                continue
            if str(node.properties.get("status") or "") == "dormant":
                continue
            capabilities = [
                edge
                for edge in outgoing.get(node.node_id, ())
                if edge.relation == "satisfiableBy"
            ]
            if not capabilities:
                self.violations.append(
                    SemanticViolation(
                        "RequirementCapabilityShape",
                        node.node_id,
                        "non-dormant evidence requirement has no executable capability",
                    )
                )

    def _validate_dependency_cycles(self) -> None:
        adjacency: dict[str, list[str]] = {}
        for edge in self.edges:
            if edge.relation == "dependsOn":
                adjacency.setdefault(edge.source, []).append(edge.target)
        visiting: set[str] = set()
        visited: set[str] = set()

        def walk(node: str, path: tuple[str, ...]) -> None:
            if node in visited:
                return
            if node in visiting:
                cycle = " -> ".join((*path, node))
                self.violations.append(
                    SemanticViolation(
                        "AcyclicEvidenceDependencyShape",
                        node,
                        f"evidence dependency cycle detected: {cycle}",
                    )
                )
                return
            visiting.add(node)
            for target in adjacency.get(node, ()):
                walk(target, (*path, node))
            visiting.discard(node)
            visited.add(node)

        for node in tuple(adjacency):
            walk(node, ())

    def _validate_authority(self) -> None:
        network = self.nodes.get("authority:network")
        activation = self.nodes.get("authority:strategy_activation")
        for node in self.nodes.values():
            if node.node_type != "Capability":
                continue
            if bool(node.properties.get("network_required")):
                if network is None or not bool(network.properties.get("granted")):
                    self.violations.append(
                        SemanticViolation(
                            "NetworkAuthorityShape",
                            node.node_id,
                            "network capability is enabled without network authority",
                        )
                    )
            if node.properties.get("risk") == "adaptive" and not node.properties.get("side_effect"):
                self.violations.append(
                    SemanticViolation(
                        "AdaptiveSideEffectShape",
                        node.node_id,
                        "adaptive capability must declare its side-effect boundary",
                    )
                )
        if activation is None:
            self.violations.append(
                SemanticViolation(
                    "AuthorityDeclarationShape",
                    "authority:strategy_activation",
                    "strategy activation authority must be explicitly represented",
                )
            )

    def validate(self) -> list[SemanticViolation]:
        """Run deterministic SHACL-inspired closed-world governance shapes."""

        # Preserve compiler-time identity violations but make validation idempotent.
        identity = [row for row in self.violations if row.shape == "UniqueIdentityShape"]
        self.violations = identity
        self._validate_edges()
        self._validate_requirements()
        self._validate_dependency_cycles()
        self._validate_authority()
        return list(self.violations)

    def fingerprint(self) -> str:
        canonical = {
            "nodes": [self.nodes[key].dict() for key in sorted(self.nodes)],
            "edges": [
                row.dict()
                for row in sorted(
                    self.edges,
                    key=lambda item: (item.source, item.relation, item.target),
                )
            ],
        }
        raw = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()

    def to_jsonld(self) -> dict[str, Any]:
        """Return a compact JSON-LD representation suitable for KG ingestion."""

        by_source: dict[str, dict[str, list[dict[str, str]]]] = {}
        for edge in self.edges:
            by_source.setdefault(edge.source, {}).setdefault(edge.relation, []).append(
                {"@id": f"{ONTOLOGY_NS}{edge.target}"}
            )
        graph_rows: list[dict[str, Any]] = []
        for node_id in sorted(self.nodes):
            node = self.nodes[node_id]
            row: dict[str, Any] = {
                "@id": f"{ONTOLOGY_NS}{node.node_id}",
                "@type": f"xushu:{node.node_type}",
                **dict(node.properties),
            }
            for relation, targets in by_source.get(node_id, {}).items():
                row[f"xushu:{relation}"] = targets
            graph_rows.append(row)
        return {
            "@context": {
                "xushu": ONTOLOGY_NS,
                "id": "@id",
                "type": "@type",
            },
            "@graph": graph_rows,
        }

    def snapshot(self, *, include_graph: bool = True) -> dict[str, Any]:
        self.validate()
        type_counts: dict[str, int] = {}
        for node in self.nodes.values():
            type_counts[node.node_type] = type_counts.get(node.node_type, 0) + 1
        result: dict[str, Any] = {
            "ontology": ONTOLOGY_NS,
            "fingerprint": self.fingerprint(),
            "valid": self.valid,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "type_counts": dict(sorted(type_counts.items())),
            "violations": [row.dict() for row in self.violations],
            "validator": "built_in_closed_world_shapes",
        }
        if include_graph:
            result["jsonld"] = self.to_jsonld()
        return result


class SemanticGovernanceCompiler:
    """Compile one MissionGraph into a typed governance knowledge graph."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def compile(self, plan: AgentPlan, mission: MissionGraph) -> SemanticGovernanceGraph:
        graph = SemanticGovernanceGraph()
        graph.add_node(
            SemanticNode(
                "objective:root",
                "Objective",
                {"goal": plan.goal, "mode": plan.mode},
            )
        )
        graph.add_node(
            SemanticNode(
                "authority:network",
                "Authority",
                {"kind": "network", "granted": bool(plan.allow_network)},
            )
        )
        graph.add_node(
            SemanticNode(
                "authority:strategy_activation",
                "Authority",
                {
                    "kind": "strategy_activation",
                    "granted": bool(plan.allow_adaptation),
                    "scope": "active_serving_strategy",
                },
            )
        )

        for key, req in mission.requirements.items():
            req_id = f"requirement:{key}"
            graph.add_node(
                SemanticNode(
                    req_id,
                    "EvidenceRequirement",
                    {
                        "key": key,
                        "domain": req.domain,
                        "priority": req.priority,
                        "status": req.status,
                        "optional": bool(req.optional),
                    },
                )
            )
            graph.add_edge("objective:root", "requiresEvidence", req_id)

        enabled = {row.name: row for row in self.registry.for_plan(plan)}
        for name, contract in enabled.items():
            cap_id = f"capability:{name}"
            graph.add_node(
                SemanticNode(
                    cap_id,
                    "Capability",
                    {
                        "name": name,
                        "domain": contract.domain,
                        "risk": contract.risk,
                        "cost": float(contract.cost),
                        "side_effect": contract.side_effect,
                        "network_required": bool(contract.network_required),
                        "information_gain": float(contract.information_gain),
                    },
                )
            )
            risk_id = f"risk:{contract.risk}"
            graph.add_node(SemanticNode(risk_id, "RiskClass", {"name": contract.risk}))
            graph.add_edge(cap_id, "hasRisk", risk_id)
            if contract.network_required:
                graph.add_edge(cap_id, "requiresAuthority", "authority:network")
            if contract.risk == "adaptive":
                graph.add_edge(cap_id, "governedBy", "authority:strategy_activation")
            for provided in sorted(contract.provides):
                graph.add_node(
                    SemanticNode(
                        f"evidence:{provided}",
                        "EvidenceKind",
                        {"key": provided},
                    )
                )
                graph.add_edge(cap_id, "providesEvidence", f"evidence:{provided}")

        for key, req in mission.requirements.items():
            req_id = f"requirement:{key}"
            for prerequisite in req.prerequisites:
                graph.add_edge(req_id, "dependsOn", f"requirement:{prerequisite}")
            alternatives = req.capabilities or ((req.tool,) if req.tool else ())
            for capability in alternatives:
                graph.add_edge(req_id, "satisfiableBy", f"capability:{capability}")

        for key, hypothesis in mission.hypotheses.items():
            hyp_id = f"hypothesis:{key}"
            graph.add_node(
                SemanticNode(
                    hyp_id,
                    "Hypothesis",
                    {
                        "key": key,
                        "domain": hypothesis.domain,
                        "status": hypothesis.status,
                        "confidence": float(hypothesis.confidence),
                    },
                )
            )
            graph.add_edge("objective:root", "considersHypothesis", hyp_id)
            for contract in enabled.values():
                if any(template.key == key for template in contract.hypotheses):
                    graph.add_edge(f"capability:{contract.name}", "diagnoses", hyp_id)

        graph.validate()
        return graph


def compile_semantic_governance(
    plan: AgentPlan,
    mission: MissionGraph,
    registry: CapabilityRegistry,
) -> dict[str, Any]:
    """Compile and return the persisted semantic-governance snapshot."""

    return SemanticGovernanceCompiler(registry).compile(plan, mission).snapshot()


def validate_with_pyshacl(jsonld: dict[str, Any]) -> dict[str, Any]:
    """Optionally validate a semantic snapshot against packaged OWL/SHACL assets.

    This is intentionally not used on the hot path.  Install with
    ``pip install -e '.[ontology]'`` for governance audits or integration tests.
    """

    try:
        from pyshacl import validate as shacl_validate
        from rdflib import Graph
    except ImportError as exc:  # pragma: no cover - optional integration
        raise RuntimeError(
            "RDF/SHACL validation requires the ontology extra: "
            "pip install -e '.[ontology]'"
        ) from exc

    data_graph = Graph().parse(data=json.dumps(jsonld), format="json-ld")
    package_root = resources.files("lingjing_harness")
    ontology_path = package_root.joinpath("ontology/xushu-governance.ttl")
    shapes_path = package_root.joinpath("ontology/xushu-shapes.ttl")
    ontology = Graph().parse(str(ontology_path), format="turtle")
    shapes = Graph().parse(str(shapes_path), format="turtle")
    conforms, results_graph, results_text = shacl_validate(
        data_graph=data_graph,
        shacl_graph=shapes,
        ont_graph=ontology,
        inference="rdfs",
        abort_on_first=False,
        allow_infos=True,
        allow_warnings=True,
    )
    return {
        "conforms": bool(conforms),
        "results_text": str(results_text),
        "result_triples": len(results_graph),
        "validator": "pyshacl+rdfs",
    }


__all__ = [
    "ONTOLOGY_NS",
    "SemanticNode",
    "SemanticEdge",
    "SemanticViolation",
    "SemanticGovernanceGraph",
    "SemanticGovernanceCompiler",
    "compile_semantic_governance",
    "validate_with_pyshacl",
]
