# Semantic Self-Evolution v2

This layer strengthens Xushu's existing evidence-driven Search/RecSys evolution without widening implicit authority.

## Design target

The optimizer should be aggressive inside a typed, measurable Search/RecSys space while the governance kernel remains conservative and independently inspectable.

```text
Goal / authority
      ↓
Mission compiler
      ↓
Semantic governance graph
  objective · evidence · capability · risk · authority · hypothesis
      ↓
closed-world shapes / optional RDF+SHACL audit
      ↓
evidence-driven agent
      ↓
measured response surface
      ↓
posterior-guided mixed genome
      ↓
stagnation-aware structural basin jump
      ↓
population / QD archive
      ↓
independent holdout + business / domain gates
      ↓
trusted memory
      ↓ explicit activation authority
active strategy
```

## Why a governance knowledge graph belongs here

The Mission Graph is deliberately an execution/evidence DAG. It answers "what evidence is still missing?". A semantic governance graph answers a different class of questions:

- What kind of thing is this requirement/capability/evidence object?
- Which capability is legally able to satisfy a requirement?
- Which evidence dependency is semantic rather than merely procedural?
- Which capabilities require network authority?
- Which adaptive actions remain governed by activation authority?
- Are there dangling identities, requirement cycles or undeclared capability boundaries?
- Can the same semantic model be exported to an enterprise KG or SHACL validator?

The runtime therefore keeps both representations instead of forcing execution state into RDF.

## Runtime ontology

The built-in semantic compiler emits stable typed nodes for:

- `Objective`
- `EvidenceRequirement`
- `EvidenceKind`
- `Capability`
- `RiskClass`
- `Authority`
- `Hypothesis`

and relations including:

- `requiresEvidence`
- `dependsOn`
- `satisfiableBy`
- `providesEvidence`
- `hasRisk`
- `requiresAuthority`
- `governedBy`
- `considersHypothesis`
- `diagnoses`

The snapshot is persisted inside `MissionGraph`, so checkpoint/resume keeps the same governance contract.

## Validation strategy

The default validator is dependency-free and runs at mission compile time. It checks closed-world references, capability satisfiability, dependency cycles and authority declarations. An invalid semantic mission is rejected before tool execution.

For external governance audits, install:

```bash
pip install -e '.[ontology]'
```

The package ships OWL/RDFS vocabulary and SHACL shapes under `lingjing_harness/ontology/`. `validate_with_pyshacl` runs RDF/SHACL validation without putting RDF reasoning on the serving hot path.

This split is intentional: semantic correctness belongs in the control plane; ranking latency must not depend on a graph reasoner.

## Structural basin routing

The previous evolver already measured every local gene arm, used bounded Beta-style historical credit, retained a quality-diversity archive and increased random mutation scale when the local surface stagnated.

A larger mutation is not necessarily a new mechanism. If the local optimum is structural, larger numeric mutations can remain in the same basin.

The v2 basin router therefore uses already-measured response-surface arms to create a bounded set of:

1. pairwise cross-capability structural jumps;
2. structural + numeric retune jumps.

Only typed registered capabilities are used. Every candidate is projected through the existing exact genome constraints. The jump candidates consume population slots before blind high-scale mutations, while the top local measured seeds are retained.

This keeps four invariants intact:

- no arbitrary generated code on the default path;
- no holdout feedback in discovery routing;
- no optimizer authority over trust thresholds;
- no activation without the existing explicit authority boundary.

## Research lineage and implementation boundary

The design incorporates ideas that have proven useful in recent open research without copying their governance assumptions:

- RecHarness: separate modification-direction routing from concrete search and use structural basin jumps under stagnation;
- GEPA: retain diverse Pareto-efficient mechanisms and learn from rich execution evidence rather than a single fixed recipe;
- AFlow: treat workflow/structure as a search space rather than assuming a permanently fixed topology;
- Ax / BoTorch: use dedicated constrained Bayesian optimization backends for expensive continuous/multi-objective search rather than rebuilding surrogate modeling inside the harness;
- Open Bandit Pipeline: keep advanced OPE/slate estimators in a dedicated causal-evaluation layer.

Xushu owns the typed domain IR, evidence isolation, governance, lifecycle and rollback semantics. External optimizers/evaluators should remain replaceable backends.

## Next hardening steps

This PR establishes the semantic and structural-search substrate. The next upgrades should build on it rather than bypass it:

1. explicit policy-as-code activation tokens instead of natural-language authority inference at the execution boundary;
2. constrained multi-objective Pareto objectives and an Ax/BoTorch backend;
3. power-aware/sequential online experiment gates;
4. Switch-DR / DRos / slate OPE backends;
5. mechanism memory that links context → hypothesis → change → segment → evidence → outcome;
6. per-action marginal evidence credit for agent deliberation instead of trajectory-wide equal credit.
