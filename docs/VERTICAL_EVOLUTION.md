# Vertical Self-Evolution

Recsys Harness evolves **search and recommendation systems**, not arbitrary repository code.

The vertical boundary is intentional: the system is allowed to change a typed Search/RecSys strategy genome, including both numeric ranking parameters and registered domain capabilities. Promotion authority remains owned by domain evaluation, independent holdout, robustness checks, permissions, verifier semantics and durable strategy memory.

## Research lineage

The implementation is a clean-room synthesis of ideas from recent open research. No third-party source code is vendored into this repository.

- **RecHarness** — useful for separating exploration direction from concrete candidate generation and for updating routing state from experimental feedback. Recsys Harness does not inherit a fixed arm taxonomy.
- **AFlow / ADAS** — useful for treating workflow or agent structure as something that can be searched rather than permanently frozen. Here that search is restricted to typed Search/RecSys capabilities.
- **GEPA-style reflective optimization** — useful for turning rich execution feedback into an optimization signal. Here credit comes from domain metrics, robustness slices and verified strategy memory rather than free-form self-judgement.

The result is deliberately not a reproduction of one paper. It combines mixed-genome search, measured response surfaces, posterior-guided routing, quality-diversity retention, isolated holdout credit assignment and durable domain memory under the Harness governance contract.

## Why parameter-only evolution was still not enough

The previous evolver removed expert-authored mutation recipes such as “increase lexical weight” or “increase freshness”, then learned numeric directions from the current dataset. That was a necessary step, but it still left the **pipeline topology fixed**.

A vertical system should also be able to answer questions such as:

- should this search workload use literal query evidence or catalog-derived expansion?
- should candidate generation stay lexical-only or add bounded semantic rescue?
- should slate diversity be category-based, semantic or hybrid?
- should recommendation profiles emphasize recent intent or long-horizon preference?
- which cold-start prior and exploration policy work for this catalog?

Hard-coding those answers in `evolution.py` would simply move the old problem one level up.

The current design therefore evolves a **mixed vertical genome**.

## 1. Mixed genome: numbers + capabilities

`SearchConfig` and `RecommendConfig` are executable strategy genomes.

### Continuous genes

Continuous fields declare:

```text
evolve_group · min · max · relative_step
```

They cover ranking/blending pressures such as lexical, semantic, graph, freshness, novelty, diversity and exploration weights. Group constraints are projected after mutation so blend mass and bounds remain valid.

### Capability genes

Structural fields declare:

```text
capability_group
```

Their legal values are discovered from `CapabilityRegistry` at runtime. The central evolver contains no list such as “query rewrite options” or “cold-start arms”.

Current structural search genome:

```text
query_strategy
candidate_strategy
rerank_strategy
```

Current structural recommendation genome:

```text
profile_strategy
candidate_strategy
cold_start_strategy
exploration_strategy
rerank_strategy
```

The important separation is:

> **Registry says what implementations exist. Evaluation decides what wins.**

Adding a registered implementation expands the search space without adding a preference branch to the optimizer.

## 2. Real vertical capabilities

The structural genes are not labels stored only for reporting. They execute inside the owned search/recommendation pipeline.

### Search

`search.query`

- `rare_focus` — discriminative query evidence; owned default.
- `literal` — tokenized user query without retrieval rewriting.
- `catalog_expand` — current-catalog, high-IDF expansion learned from anchored items.

`search.candidate`

- `postings_union` — owned lexical/category postings only.
- `semantic_rescue` — bounded semantic candidates, but only after lexical/catalog evidence anchors the query.

`search.rerank`

- `category_mmr`
- `semantic_mmr`
- `hybrid_mmr`

Semantic rescue intentionally cannot turn an unknown query into a result solely because of hash-vector collision. The safety property of “no evidence, no retrieval” remains intact.

### Recommendation

`recommend.profile`

- `recency_balanced`
- `recent_intent`
- `long_horizon`

`recommend.candidate`

- `full_pool`
- `evidence_union`

`recommend.cold_start`

- `quality_freshness`
- `discovery_prior`
- `fresh_explore`

`recommend.exploration`

- `stable_fresh`
- `novelty_seek`
- `coverage_seek`

`recommend.rerank`

- `category_mmr`
- `semantic_mmr`
- `hybrid_mmr`

These are bounded domain components, not arbitrary generated code. Their selection is evolvable; their execution semantics remain inspectable and testable.

## 3. Measure every gene before trusting it

Before population search, the evolver measures a local response surface on the **discovery split**.

For a continuous gene:

```text
current genome
  ├─ field ↑ schema-derived step
  └─ field ↓ schema-derived step
```

For a capability gene:

```text
current genome
  ├─ capability = registered alternative A
  ├─ capability = registered alternative B
  └─ ...
```

Every alternative executes the full pipeline. Search capability mutations re-run query processing, candidate generation and ranking. Recommendation capability mutations re-run profile construction, candidate generation, cold-start/exploration logic and ranking.

This matters: a structural mutation cannot receive credit merely because its config string changed.

Each measured arm records:

```text
field · kind · direction/choice
objective_delta · robustness
historical_prior · posterior_sample · routing_score
```

## 4. No central preference table

There is no fixed list like:

```text
GOOD_SEARCH_ARMS = {...}
GOOD_REC_ARMS = {...}
```

and no source-code rule saying a particular workload should choose semantic reranking, novelty exploration or recent-intent profiling.

The optimizer only understands generic gene semantics:

```text
continuous gene → perturb within schema
capability gene → try registered alternative
```

Search/RecSys expertise lives where it belongs:

- capability implementation;
- domain metrics;
- evaluation slices;
- constraints and safety invariants.

Preference emerges from measured performance.

## 5. Posterior-guided mixed routing

Trusted historical strategies contribute bounded Beta-style priors.

For numeric genes, memory learns directional tendencies:

```text
lexical:up
freshness:down
```

For capability genes, memory learns validated choices:

```text
rerank_strategy=hybrid_mmr
cold_start_strategy=discovery_prior
```

Routing combines current discovery evidence with a sampled historical posterior. Current-domain evidence remains dominant, so yesterday's winner cannot override today's measurements.

Historical memory therefore changes **where exploration budget goes**, not authority, holdout membership or verifier rules.

## 6. Quality-diversity across mechanisms

A single scalar lineage quickly collapses to one local mechanism. The evolver instead keeps a bounded archive keyed by mutation signature.

Examples:

```text
lexical:up + semantic:down
query_strategy=catalog_expand
candidate_strategy=semantic_rescue + rerank_strategy=hybrid_mmr
profile_strategy=recent_intent + exploration_strategy=novelty_seek
```

Only the strongest candidate for a signature survives. Later generations may draw parents from several distinct mechanisms.

This keeps structural diversity without turning memory into an unbounded experiment log.

## 7. Stagnation can widen both numeric and structural search

If the local response surface is effectively flat, the system treats the current strategy as a local basin.

The next population increases mutation radius and is more willing to switch registered capability genes. There is still no special-case “architecture jump list”; available structural moves come from the current registry/schema.

## 8. Evaluation slices must match the behavior

A behavior cannot receive valid credit from a dataset slice that never exercises it.

That is especially important for recommendation cold start. Warm-user evaluation alone cannot tell whether a cold-start policy is better.

The current recommendation evolver therefore adds an explicit cold-start quality slice. Discovery and holdout use different synthetic cold-user identities so exploration/cold-start behavior is measured without leaking the holdout identity into search-time routing.

The full flow is:

```text
discovery warm users / queries
+ discovery cold-start slice
        ↓
mixed response surface
        ↓
posterior-guided routing
        ↓
population + QD archive
        ↓
discovery winner

independent holdout
+ separate cold-start holdout identity
        ↓
regression / robustness gates
        ↓
trusted strategy memory
        ↓
optional activation (separate user authority)
```

Holdout results never route arms or choose the discovery winner.

## 9. Backward-safe evolution

Stored trusted strategies may outlive a capability implementation.

If a persisted config references a capability that is no longer registered, runtime execution fails closed to the owned default. It does not dynamically import an unknown implementation and does not silently expand the trusted execution surface.

Older numeric-only strategies also remain loadable: newly introduced capability fields use owned defaults when absent.

## 10. What evolves

- continuous Search/RecSys ranking configuration;
- query processing strategy;
- candidate generation strategy;
- slate reranking strategy;
- recommendation profile horizon;
- cold-start policy;
- exploration policy;
- which dimensions receive experiment budget;
- posterior priors from independently validated strategies;
- bounded quality-diversity mechanisms;
- procedural strategy memory after verification.

## 11. What does not evolve implicitly

- user permissions;
- network authority;
- holdout membership;
- verifier semantics;
- trust thresholds;
- tool risk classes;
- durable checkpoint / recovery / fencing rules;
- arbitrary source-code execution.

Those are Harness governance, not optimization genes.

## 12. Extension contract

A new vertical capability should not require a new central-agent branch or a new optimizer preference table.

A new capability should:

1. belong to a typed Search/RecSys capability group;
2. be registered with a deterministic owned implementation;
3. be referenced by a `capability_group` field in a domain config when it is evolvable;
4. execute in the real pipeline under evaluation;
5. have a discovery metric that can exercise it;
6. have an independent holdout slice and regression invariants;
7. enter trusted memory only after verification;
8. remain separately permissioned before activation.

This is the mechanism for extending query rewriting, retrieval, reranking, cold-start, exploration and future segment-specific policies without making the central Harness a pile of special cases.

## Non-goal

Recsys Harness is **not** a general autonomous software engineer. It does not grant the optimizer permission to invent arbitrary Python, edit governance code or rewrite its own verifier.

Its autonomy is deliberately strongest inside the Search/RecSys domain where evidence, metrics, experiments and rollback semantics are strongest.
