# Vertical Self-Evolution

Recsys Harness evolves **search and recommendation systems**, not arbitrary repository code.

The vertical boundary is intentional: the system is allowed to change a typed Search/RecSys strategy genome, including both numeric ranking parameters and registered domain capabilities. Promotion authority remains owned by domain evaluation, identity-isolated holdout, robustness checks, behavior-matched gates, permissions, verifier semantics and durable strategy lifecycle rules.

## Research lineage

The implementation is a clean-room synthesis of ideas from recent open research. No third-party source code is vendored into this repository.

- **RecHarness** — useful for separating exploration direction from concrete candidate generation and for updating routing state from experimental feedback. Recsys Harness does not inherit a fixed arm taxonomy.
- **AFlow / ADAS** — useful for treating workflow or agent structure as something that can be searched rather than permanently frozen. Here that search is restricted to typed Search/RecSys capabilities.
- **GEPA-style reflective optimization** — useful for turning rich execution feedback into an optimization signal. Here credit comes from domain metrics, robustness slices and verified strategy memory rather than free-form self-judgement.

The result is deliberately not a reproduction of one paper. It combines mixed-genome search, measured response surfaces, posterior-guided routing, quality-diversity retention, identity-isolated holdout credit assignment and durable domain memory under the Harness governance contract.

## Why parameter-only evolution was still not enough

The first data-driven evolver removed expert-authored recipes such as “increase lexical weight” or “increase freshness”, then learned numeric directions from the current dataset. That was necessary, but it still left pipeline topology fixed.

A vertical system should also be able to test questions such as:

- should this search workload use literal query evidence or catalog-derived expansion?
- should candidate generation stay lexical-only or add bounded semantic rescue?
- should slate diversity be category-based, semantic or hybrid?
- should recommendation profiles emphasize recent intent or long-horizon preference?
- which cold-start prior and exploration policy work for this catalog?

Hard-coding those answers in the optimizer would simply move the old rule table one level up. The current design therefore evolves a **mixed vertical genome** while keeping trust/governance outside that genome.

## 1. Mixed genome: numbers + capabilities

`SearchConfig` and `RecommendConfig` are executable strategy genomes.

### Continuous genes

Continuous fields declare:

```text
evolve_group · min · max · relative_step
```

The group has semantic meaning.

- `blend` means the fields participate in one normalized scoring mass.
- `independent` means a bounded pressure may move without rescaling unrelated genes.

This distinction is important. Recommendation `cold_start` pressure is inactive for warm users, so it is an independent gene. Mutating cold-start pressure must not silently alter profile, graph, freshness or other warm-user weights.

### Capability genes

Structural fields declare:

```text
capability_group
```

Their legal values are discovered from `CapabilityRegistry` at runtime. The central evolver contains no preference list such as “query rewrite options” or “cold-start arms”.

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

> **Registry says what implementations exist. Evaluation decides what wins.**

Adding a registered implementation expands the experiment space without adding a preference branch to the optimizer.

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

Semantic rescue intentionally cannot turn an unknown query into a result solely because of hash-vector collision. The invariant remains: **no grounded retrieval evidence, no semantic rescue**.

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

Candidate capability output is normalized before scoring: unknown/ineligible/seen IDs are removed and duplicate IDs are collapsed. A structural component therefore cannot accidentally create duplicate slate candidates by returning the same ID twice.

These remain bounded domain components, not arbitrary generated code. Their selection is evolvable; execution semantics remain inspectable and testable.

## 3. Schema validation precedes evolution

A strategy genome is not trusted merely because it came from memory or a previous version.

Before execution/evolution, the current schema validates:

```text
continuous gene
  → numeric?
  → finite?
  → within declared bounds?

capability gene
  → group exists?
  → implementation still registered?
  → otherwise owned default is the effective choice
```

Unknown removed capability names may resolve to an owned default for safe execution, but an **active persisted strategy is stricter**: if canonicalization changes the effective config, the old active fingerprint is retired because it no longer describes what will actually run.

This prevents a subtle failure mode where UI/memory says “active strategy X” while runtime silently executes strategy Y.

## 4. Exact constrained projection

Numeric mutation is only valid if the resulting genome still satisfies its constraints.

For each normalized blend group the evolver requires:

```text
∀ d: low_d ≤ x_d ≤ high_d
Σ x_d = original_group_mass
```

A one-shot “normalize, clip, correct once” algorithm is insufficient because clipping the correction can move the sum away from target again.

The hardened projector therefore uses bounded capacity redistribution:

1. clip every member to its declared bounds;
2. calculate remaining positive/negative mass;
3. calculate each member's remaining capacity toward the required direction;
4. distribute mass proportionally across available capacity;
5. repeat until exact group mass is reached or reject an impossible projection.

Extreme mutation is therefore allowed to explore the boundary without corrupting the semantics of the blend.

## 5. Measure every gene before trusting it

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

Every alternative executes the full pipeline. Search mutations re-run query processing, candidate generation and ranking. Recommendation mutations re-run profile construction, candidate generation, cold-start/exploration logic and ranking.

A structural mutation cannot receive credit merely because its config string changed.

Each measured arm records:

```text
field · kind · direction/choice
objective_delta · robustness
historical_prior · posterior_sample · routing_score
```

## 6. No central preference table

There is no fixed list like:

```text
GOOD_SEARCH_ARMS = {...}
GOOD_REC_ARMS = {...}
```

and no source-code rule saying a workload should choose semantic reranking, novelty exploration or recent-intent profiling.

The optimizer only understands generic gene semantics:

```text
continuous gene → perturb within schema
capability gene → try registered alternative
```

Search/RecSys expertise lives where it belongs:

- capability implementation;
- domain metrics;
- evaluation slices;
- schema constraints;
- safety/trust invariants.

Preference emerges from measured performance.

## 7. Posterior-guided mixed routing with untrusted durable memory

Trusted historical strategies contribute bounded Beta-style priors.

For numeric genes, memory can learn directional tendencies:

```text
lexical:up
freshness:down
```

For capability genes, memory can learn validated choices:

```text
rerank_strategy=hybrid_mmr
cold_start_strategy=discovery_prior
```

Routing combines current discovery evidence with a sampled historical posterior. Current-domain evidence remains dominant, so yesterday's winner cannot override today's measurements.

Durable memory is nevertheless treated defensively. A legacy/corrupted row may contain a non-numeric value, non-finite number, removed capability or incomplete schema. Such a row must not crash the new run. Invalid rows are skipped during routing/seeding; active strategies are separately governed by the stricter lifecycle in section 12.

Historical memory therefore changes **where exploration budget goes**, not authority, holdout membership, verifier rules or executable schema.

## 8. Quality-diversity across mechanisms

A single scalar lineage quickly collapses to one local mechanism. The evolver instead keeps a bounded archive keyed by mutation signature.

Examples:

```text
lexical:up + semantic:down
query_strategy=catalog_expand
candidate_strategy=semantic_rescue + rerank_strategy=hybrid_mmr
profile_strategy=recent_intent + exploration_strategy=novelty_seek
```

Only the strongest candidate for a signature survives. Later generations may draw parents from several distinct mechanisms.

Population expansion also has a bounded attempt count. A small/degenerate genome cannot trap the optimizer in an infinite loop merely because it cannot generate enough unique candidates.

## 9. Evaluation identity is the isolation primitive

“Two lists called discovery and holdout” are not enough. Independence is about **identity**.

### Search query labels

If the same query appears multiple times in imported labels, splitting rows independently can place the same query on both sides. Catalog ingestion therefore first canonicalizes labels:

```text
same query
  + relevance set A
  + relevance set B
        ↓
one query identity
  + merged eligible relevance set
```

The evolution splitter then defensively uniques by query identity again before deterministic discovery/holdout assignment.

### Recommendation users

Warm-user splitting is unique by user ID.

Synthetic cold-start evaluation has another collision hazard: an internal synthetic ID could accidentally already exist as a real user and inherit warm history. Cold evaluation therefore checks the known-user set and deterministically chooses a collision-free identity.

The invariant is:

> **One evaluation identity can belong to at most one side of a discovery/holdout boundary.**

## 10. Evaluation slices must match behavior

A behavior cannot receive valid credit from a slice that never exercises it.

That is especially important for recommendation cold start. Warm-user evaluation alone cannot tell whether a cold-start policy is better.

The recommendation evolver therefore evaluates:

```text
discovery warm users
+ collision-safe discovery cold identities
        ↓
mixed response surface
        ↓
posterior-guided routing
        ↓
population + QD archive
        ↓
discovery winner

independent holdout warm users
+ disjoint collision-safe holdout cold identities
        ↓
quality / coverage / robustness
+ explicit holdout cold_start_quality_delta
        ↓
trusted strategy memory
        ↓
optional activation
```

Holdout outcomes never route arms or choose the discovery winner.

More importantly, cold-start is now part of the **gate**, not only the report:

- a material holdout cold-start regression blocks safety even when warm aggregate metrics improve;
- a genuine cold-start-only gain can earn trust when the independent cold holdout confirms it and the remaining regression/robustness requirements pass.

## 11. Warm and cold objectives are not the same gene scope

`cold_start` pressure contributes only when a user has no history. Treating it as a member of the warm blend creates hidden causal coupling:

```text
change cold_start
  → normalize full blend
  → profile/graph/freshness change
  → warm ranking changed even though cold_start is inactive
```

The hardened genome makes `cold_start` an independent bounded gene. Cold-start experiments can therefore be credited to cold-start behavior rather than an accidental warm-weight redistribution.

This is a general design rule: **only genes that share the same semantic scoring scope may share normalization mass.**

## 12. Active strategy lifecycle

Trusted memory and active execution are different states.

An active strategy is checked at load time:

```text
stored active config
        ↓
current typed schema
  · finite numeric values
  · declared bounds
  · registered capabilities
        ↓
effective config == stored config ?
  ├─ no  → retire old fingerprint → owned default
  └─ yes → execute + periodic regression validation
```

A removed capability therefore does not remain “active via fallback”. Fallback is safe execution behavior, but changing the effective strategy invalidates the old active fingerprint and requires future re-evaluation.

Periodic active validation compares against the owned default. Search checks quality/recall. Recommendation checks aggregate quality, coverage **and cold-start quality**. A strategy that regresses cold users can therefore be retired even when warm aggregate metrics look acceptable.

## 13. What evolves

- continuous Search/RecSys ranking configuration;
- query processing strategy;
- candidate generation strategy;
- slate reranking strategy;
- recommendation profile horizon;
- cold-start policy and pressure;
- exploration policy;
- which dimensions receive experiment budget;
- posterior priors from independently validated strategies;
- bounded quality-diversity mechanisms;
- procedural strategy memory after verification.

## 14. What does not evolve implicitly

- user permissions;
- network authority;
- evaluation identity / holdout membership;
- verifier semantics;
- trust thresholds;
- tool risk classes;
- durable checkpoint / recovery / fencing rules;
- arbitrary source-code execution.

Those are Harness governance, not optimization genes.

## 15. Failure-oriented validation

The evolution suite intentionally tests cases that a normal happy-path benchmark will miss:

- duplicate query identity leakage across splits;
- extreme bounded projection at several clipped dimensions;
- cold-start mutation accidentally changing warm weights;
- malformed trusted-memory rows;
- synthetic cold identity colliding with a real user;
- cold-start-only improvement credit;
- warm improvement masking holdout cold regression;
- invalid/removed active capability retirement;
- active cold-start rollback;
- default retrieval invariants after structural evolution changes.

These tests run together with the full Agent Harness, API, durability, CLI, package and product hygiene suite. Evolution is not considered correct in isolation from the runtime that owns it.

## 16. Extension contract

A new vertical capability should not require a new central-agent branch or optimizer preference table.

A new capability should:

1. belong to a typed Search/RecSys capability group;
2. be registered with a deterministic owned implementation;
3. be referenced by a `capability_group` field when it is evolvable;
4. execute in the real pipeline under evaluation;
5. define a discovery metric/slice that actually exercises it;
6. define an independent identity-isolated holdout slice and regression invariants;
7. declare whether its numeric pressures share a normalization scope or are independent;
8. produce candidate/output identities that can be canonicalized and validated;
9. enter trusted memory only after verification;
10. remain separately permissioned before activation.

This is the mechanism for extending query rewriting, retrieval, reranking, cold-start, exploration and future segment-specific policies without making the central Harness a pile of special cases.

## Non-goal

Recsys Harness is **not** a general autonomous software engineer. It does not grant the optimizer permission to invent arbitrary Python, edit governance code or rewrite its own verifier.

Its autonomy is deliberately strongest inside the Search/RecSys domain where evidence, metrics, experiment identity, validation and rollback semantics are strongest.
