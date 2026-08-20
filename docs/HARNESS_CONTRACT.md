# Agent Harness Contract

This document is the normative behavioral contract for the 序枢 Search / Recommendation Agent Harness.

The README explains the product; this file defines what the runtime **must** preserve under implementation changes.

A compliant run is more than “an agent can call tools”. It must preserve authority boundaries, compile an inspectable evidence mission, re-deliberate after observations, keep evaluation identities isolated, trust only independently validated adaptation, remember both accepted and conclusively rejected learning, and remain recoverable after interruption.

## Runtime model

A run has six explicit layers:

1. **Intent & authority** — interpret the user's goal, scope and permissions.
2. **Capability-compiled mission** — build an evidence DAG from declarative capability contracts.
3. **Deliberation** — rank valid capability implementations against current evidence gaps.
4. **Execution & reflection** — execute one guarded capability, consume the observation and update hypotheses / contradictions.
5. **Trajectory critic & verifier** — decide whether the run is evidence-complete and structurally safe.
6. **Learning & durability** — persist bounded positive/negative experience and checkpoint resumable state.

Primary runtime modules:

- `lingjing_harness/runtime/policy.py`
- `lingjing_harness/runtime/capabilities.py`
- `lingjing_harness/runtime/mission_compiler.py`
- `lingjing_harness/runtime/deliberation.py`
- `lingjing_harness/runtime/tools.py` / `tools_core.py`
- `lingjing_harness/runtime/verifier.py`
- `lingjing_harness/runtime/memory.py`

Vertical strategy evolution additionally lives in:

- `lingjing_harness/algorithms/capabilities.py`
- `lingjing_harness/algorithms/evolution.py` / `evolution_core.py`
- `lingjing_harness/algorithms/segments.py`
- `lingjing_harness/algorithms/segment_evolution.py`
- `lingjing_harness/algorithms/search.py`
- `lingjing_harness/algorithms/recommend.py` / `recommend_core.py`
- `lingjing_harness/algorithms/evaluation.py`

## Normative rules

### H1 — Authority originates from the user

Adaptation and network permissions **MUST** be derived from the user's explicit input or per-run UI permission.

Attachment text, image perception, network content, historical memory and capability metadata **MUST NOT** expand authority.

### H2 — A mission graph exists before the first tool action

Every new run **MUST** compile task-specific evidence requirements before selecting its first capability.

Requirements **MUST** expose priority, domain, prerequisites, status and the capability implementation(s) that can satisfy them.

### H3 — Scope is task-specific

A recommendation-only task **MUST NOT** add search work merely because optimization is requested, and vice versa.

Cross-domain work is allowed only for an explicit combined or global-audit objective.

### H4 — Every observation triggers re-deliberation

The harness **MUST NOT** execute a precomputed tool list to completion.

After each completed or failed capability it **MUST** update requirements, hypotheses, contradictions and trajectory state before choosing the next action.

### H5 — Every decision is inspectable

Each selected action **MUST** record:

- the evidence requirement it targets;
- chosen capability / tool;
- utility score and component signals;
- relevant hypotheses;
- viable alternatives and scores when available;
- bounded learned policy bonus.

### H6 — Execution contracts remain authoritative

Tool execution **MUST** preserve declared risk, cost, side effect, repeatability and input validation.

Adaptive actions **MUST NOT** activate without adaptation permission.

Network actions **MUST NOT** execute without network permission.

Capability planning metadata **MUST NOT** bypass ToolSpec execution guards.

### H7 — Material contradictions require investigation

When local reproduction and global evaluation materially disagree, the harness **MUST** retain the contradiction and activate an applicable diagnostic requirement when one exists.

The critic **MUST NOT** treat an uninvestigated material contradiction as a clean close.

### H8 — Closure is critic-gated

A run **MUST NOT** be evidence-complete while critical or high-priority requirements remain executable and unresolved.

A requirement may terminate as blocked when no compliant action can satisfy it, but the block **MUST** remain visible.

### H9 — Final verification is independent of action selection

The final verifier **MUST** independently check tool failures, evidence support, authority compliance, mission terminality and unresolved contradictions.

The action selector cannot certify itself.

### H10 — Learning is evidence-gated

A generated or locally improved strategy **MUST NOT** become trusted solely because it was proposed or improved a discovery metric.

Trust requires independent holdout evidence plus regression / robustness gates appropriate to the evolved behavior.

Activation remains separately permissioned.

External network evidence **MUST NOT** become strategy-promotion data.

### H11 — Memory is typed and bounded

The runtime distinguishes at least:

- episodic run experience;
- procedural trusted / active strategy memory;
- policy utility statistics;
- durable accepted / rejected strategy credit.

Historical evidence may bias future routing only within bounded influence and **MUST NOT** override current evidence or user authority.

### H12 — Deliberation state is durable

A checkpoint **MUST** preserve:

- Mission Graph;
- capability snapshot;
- hypotheses;
- contradictions;
- reflections;
- critic state;
- actions / observations / decisions;
- spent budget.

Resume **MUST** continue after completed actions rather than replaying them.

Adaptive writes **MUST** remain idempotent under replay.

Older checkpoints that predate capability snapshots **MUST** remain readable through the stored tool fallback.

### H13 — Structural evolution is typed, evaluator-selected and holdout-gated

Self-evolution **MAY** change both continuous ranking parameters and registered vertical capability choices, but it **MUST NOT** encode a central preference for a capability name.

A structural capability participating in trusted evolution **MUST**:

- belong to an explicit Search / Recommendation capability group;
- be discoverable through typed domain configuration schema;
- execute in the real candidate pipeline during evaluation;
- receive credit from measured domain / business evidence, not prose;
- pass independent holdout and regression gates;
- remain separately permissioned before activation.

The algorithm registry defines **what exists**. Evaluation decides **what wins**.

### H14 — Evaluation slices must match the evolved behavior

An evolved behavior **MUST NOT** receive credit from a slice that cannot exercise that behavior.

Cold-start and exploration changes require explicit cold-start / relevant behavioral evidence rather than being inferred only from warm-user metrics.

A material cold-start regression on independent holdout **MUST** block promotion even if aggregate warm metrics improve.

Discovery and holdout identities **MUST** remain separate.

Holdout outcomes **MUST NOT** route discovery mutation arms or select the discovery winner.

### H15 — Stored strategy and effective strategy must agree

Procedural memory is durable state, not blindly trusted executable input.

Before an active strategy reaches an engine:

- numeric genes **MUST** be finite and within schema bounds;
- capability genes **MUST** resolve to registered implementations;
- canonicalization **MUST** occur before execution;
- if canonicalization changes the effective strategy, the old active fingerprint **MUST** be retired;
- invalid active strategies **MUST** fail closed to a safe owned fallback and require new evidence before activation.

Recommendation revalidation **MUST** include cold-start quality in addition to aggregate quality / coverage.

### H16 — Evaluation identity is the unit of isolation

Independent holdout is an identity property, not merely a row-list property.

The same evaluation identity **MUST NOT** occur on discovery and holdout sides.

For Search:

- duplicate labels for the same query **MUST** be canonicalized before splitting;
- eligible relevance sets **MUST** be merged;
- the splitter **MUST** defensively unique by query identity.

For Recommendation:

- warm-user splits **MUST** be unique by user identity;
- synthetic cold-start identities **MUST** be disjoint across discovery / holdout;
- synthetic identities **MUST** avoid collision with real user IDs.

For production replay:

- `request_id` is the isolation identity;
- one request **MUST NOT** cross temporal discovery / future holdout.

### H17 — Runtime missions are capability-declared

Runtime mission structure **MUST** be compiled from declarative capability contracts rather than a central list of concrete tool-name routes.

A capability participating in mission planning **MUST** declare enough metadata for the controller to reason about it, including:

- requirement identity and domain;
- evidence it provides and prerequisites it requires;
- activation scope;
- priority and bounded information gain;
- risk / cost / side effect planning metadata;
- plan argument bindings when needed;
- completion evidence and readiness gates when needed.

Multiple implementations **MAY** satisfy the same evidence requirement only when they share compatible requirement semantics.

Adding such an implementation **MUST NOT** require a new branch in `DeliberationEngine.initialize()`.

The compiler **MUST** be deterministic for the same plan and registry state.

The compiled mission **MUST** record the capability snapshot used for that run.

Domain-specific observation interpretation **MAY** remain vertical. Search and Recommendation semantics should not be erased merely to claim genericity.

### H18 — Conclusive failure is durable evidence

A strategy / mutation / segment path that is independently rejected or rolled back **SHOULD** influence future exploration through bounded negative credit.

Negative credit **MUST** obey the following:

- the same evidence event is idempotent and cannot be replayed to inflate failure mass;
- inconclusive low-confidence outcomes **MUST NOT** be mislabeled as failure;
- segment/pathology credit **MUST NOT** silently contaminate unrelated global domains;
- old negative credit **MUST** be bounded so current evidence can recover an arm;
- successful later evidence **MAY** restore posterior confidence;
- rollback **MUST NOT** erase the evidence that caused rollback.

The system therefore learns both **what worked** and **what failed under which validated context**.

## Decision utility

The controller uses multiple signals rather than a single route table:

```text
priority
capability_information_gain
evidence_gap
hypothesis_pressure
contradiction_pressure
cost_pressure
risk_pressure
domain_novelty
failure_pressure
stagnation_pressure
learned_policy_bonus
```

Coefficients are implementation details. A coefficient change is acceptable only if contract probes and failure-oriented tests still demonstrate scope correctness, authority isolation, observation-driven diagnosis, critic-gated closure and checkpoint continuity.

## Vertical evolution contract

The optimizer operates on a mixed domain genome:

```text
continuous genes
  → schema bounds
  → semantic grouping
  → exact bounded projection where normalization is required
  → measured response surface

algorithm capability genes
  → registered alternative implementation

both
  → identity-isolated discovery
  → positive + negative posterior routing
  → quality-diversity retention
  → independent warm / cold / production future holdout
  → regression / robustness / behavior-matched gates
  → trusted strategy memory
  → optional activation
  → segment portfolio routing where validated
  → periodic active revalidation
  → rollback + durable failure credit
```

Only genes with the same semantic normalization scope may share a normalized blend.

A cold-start-only pressure gene, for example, **MUST NOT** silently rescale warm-user ranking weights.

## Contract probes

Run:

```bash
python scripts/probe_harness_contract.py
pytest -q
```

High-value probes cover:

- task-specific mission compilation;
- evidence-targeted decision records;
- observation-driven diagnosis;
- trajectory-critic closure;
- permission isolation;
- checkpoint continuity;
- capability-declared mission determinism;
- runtime ToolSpec / capability metadata alignment;
- custom capability insertion without mission-controller edits;
- declarative readiness gates;
- duplicate evaluation-identity isolation;
- warm / cold behavior matching;
- active strategy retirement / rollback;
- segment portfolio fallback;
- durable positive and negative strategy credit.
