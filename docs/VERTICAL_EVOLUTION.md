# Vertical Self-Evolution

This document defines how Recsys Harness evolves **search and recommendation systems** without turning the product into a generic code-writing agent.

The vertical boundary is deliberate: evolution may change a declared search/recommendation strategy genome, but promotion authority remains owned by domain evaluation, holdout isolation, robustness checks, permissions, and durable strategy memory.

## Research lineage

The implementation is a clean-room synthesis of ideas from recent open research. No third-party source code is vendored into this repository.

- **RecHarness** — `6lyc/RecHarness`, MIT. The useful idea is to separate *which direction to explore* from *how to instantiate a candidate*, and to update a bandit-style routing state from experiment feedback. We do **not** copy its fixed mutation-arm taxonomy; our arms are generated from the current domain schema.
- **AFlow** — `FoundationAgents/AFlow`, MIT, ICLR 2025 Oral. The useful idea is feedback-driven workflow search rather than a permanently fixed execution recipe. We keep the search space vertical and typed instead of evolving arbitrary agent code.
- **ADAS** — `ShengranHu/ADAS`, Apache-2.0, ICLR 2025. The useful idea is that an agent architecture can itself be searched and improved. Recsys Harness applies this principle only inside explicit search/recommendation capability contracts.
- **GEPA** — `gepa-ai/gepa`, MIT, ICLR 2026. The useful idea is to use rich execution feedback as an optimization signal. Here, the equivalent feedback is domain response surfaces, regression slices, robustness outcomes, and validated strategy memory.

The result is intentionally not a reproduction of any one paper. It combines bandit routing, response-surface measurement, quality-diversity retention, independent holdout credit assignment, and durable domain memory under the existing Harness safety contract.

## What was wrong with the old evolver

The previous implementation contained expert-authored recipes such as “increase lexical weight, decrease semantic weight” or “increase freshness and novelty”. Those recipes can be useful as a baseline, but they have three structural problems:

1. they encode yesterday's expert intuition in source code;
2. a new ranking signal requires editing the evolver itself;
3. the same mutation direction is tried even when the current catalog says the opposite.

That is optimization **by rule**, not self-evolution.

## 1. A self-describing vertical genome

`SearchConfig` and `RecommendConfig` are the strategy genomes. Evolvable fields declare metadata next to the field itself:

```text
evolve_group · min · max · relative_step
```

The evolution engine discovers dimensions with dataclass reflection. There is no second list of search parameters or recommendation parameters in `evolution.py`.

Two constraint types are currently sufficient for the owned rankers:

- `blend` — weights are projected back to the original group mass after mutation;
- `independent` — bounded parameters such as slate-diversity pressure can move independently.

If a new owned ranker adds a signal and gives it evolution metadata, the response-surface engine sees it automatically.

## 2. Measure before proposing

Before a population search starts, the evolver measures a local response surface on the **discovery split**.

For every discovered dimension `d`, it evaluates:

```text
current config
  ├─ d ↑ one schema-derived step
  └─ d ↓ one schema-derived step
```

The step size comes from the field's current magnitude and declared bounds; it is not a hand-authored search/recommendation recipe.

Search responses are scored with the owned ranking objective built from NDCG/recall plus robustness penalties. Recommendation responses use the owned coverage/diversity/freshness/novelty objective plus robustness penalties.

This produces a vertical semantic gradient:

```text
(field, direction, objective_delta, robustness)
```

“Semantic” here means the mutation has domain meaning (`freshness:up`, `graph:down`, etc.); the credit still comes from real recommender/search evaluation rather than model prose.

## 3. Dynamic posterior-routed arms

Mutation arms are generated at runtime:

```text
<field>:up
<field>:down
```

There is no fixed `ARMS = {...}` taxonomy.

Validated strategy memory contributes a Beta-style prior. If previously trusted configurations repeatedly moved a field in one direction, that direction receives a stronger prior. The current catalog's measured response remains dominant, so historical memory cannot override new evidence.

Routing combines:

```text
current discovery response
+ sampled historical posterior
```

The router therefore learns across runs while remaining data-sensitive on every new run.

## 4. Quality-diversity instead of one scalar lineage

Keeping only the single best candidate makes evolution brittle: many distinct mechanisms collapse into one local optimum.

The engine therefore maintains a small archive keyed by **mutation signature**. Examples:

```text
lexical:up + semantic:down
freshness:up + popularity:down
profile:up + graph:up
```

Only the best candidate for a signature survives in that archive. The next generation can draw parents from multiple mechanisms rather than only the global scalar winner.

This is a compact quality-diversity mechanism, not an unbounded archive.

## 5. Stagnation triggers wider exploration

If every one-dimensional response is effectively flat, the system treats the current configuration as a local basin. Candidate generation then increases mutation radius across automatically discovered dimensions.

The important distinction from a hard-coded “architecture jump” list is that the jump surface is still generated from the current vertical schema. Future structural capabilities can expose their own typed genome instead of adding special cases to the central evolver.

## 6. Credit assignment remains isolated

Self-evolution never gets to grade itself on the same evidence used to propose changes.

```text
discovery split
  → response surface
  → dynamic routing
  → candidate population / QD archive
  → candidate selection

independent holdout
  → regression checks
  → robustness gate
  → trusted strategy memory
  → optional activation (permissioned separately)
```

The held-out set is never used to route mutation arms or pick the discovery winner.

A candidate can be interesting without being trusted. A trusted candidate can be stored without being activated. Activation is a separate authority decision.

## 7. What evolves, and what does not

### Evolves

- search/recommendation strategy configuration;
- which domain dimensions receive exploration budget;
- historical directional priors from validated strategies;
- retained mutation mechanisms in the bounded archive;
- task-level procedural memory after independent validation.

### Does not evolve implicitly

- user permissions;
- network authority;
- holdout data membership;
- trust thresholds and verifier semantics;
- tool risk classes;
- durable recovery/fencing rules.

Those are Harness governance, not optimization parameters.

## 8. Extension contract

A new vertical capability should not require a new central-agent branch.

For a new owned ranker or policy:

1. expose a typed configuration genome;
2. declare evolvable field metadata and bounds;
3. provide a discovery evaluator and an independent holdout evaluator;
4. define robustness/regression invariants;
5. let the generic response-surface/router/archive machinery generate mutations;
6. promote only through the existing verifier and permission boundary.

This is the direction for future reranking, query rewriting, cold-start policies, exploration policies, and segment-specific ranking strategies.

## Non-goal

The project is **not** trying to become a general autonomous software engineer. Arbitrary repository edits are intentionally outside the trusted self-evolution path. The product is a vertical Search/RecSys Harness whose autonomy is strongest exactly where its evidence and evaluation contracts are strongest.
