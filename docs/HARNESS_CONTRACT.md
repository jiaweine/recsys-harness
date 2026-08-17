# Agent Harness Contract

This document is the normative behavioral contract for the Recsys Harness runtime.
The README explains the product; this file defines what the runtime **must** do.

The contract is intentionally stricter than “an agent can call tools”. A compliant
run must preserve authority boundaries, maintain inspectable deliberation state,
collect enough evidence to justify closure, and remain recoverable after process
interruption.

## Runtime model

A run is composed of six explicit layers:

1. **Intent & authority** — compile the user's goal, scope and permissions.
2. **Mission graph** — define evidence requirements and their dependencies.
3. **Deliberation** — rank currently valid actions against evidence gaps and hypotheses.
4. **Execution & reflection** — execute one guarded capability, consume the observation, update the mission.
5. **Trajectory critic & verifier** — decide whether the run is evidence-complete and structurally safe.
6. **Learning & durability** — record bounded experience and checkpoint resumable state.

The runtime implementation lives primarily in:

- `lingjing_harness/runtime/harness.py`
- `lingjing_harness/runtime/policy.py`
- `lingjing_harness/runtime/deliberation.py`
- `lingjing_harness/runtime/tools.py`
- `lingjing_harness/runtime/verifier.py`
- `lingjing_harness/runtime/memory.py`

Vertical strategy evolution additionally lives in:

- `lingjing_harness/algorithms/capabilities.py`
- `lingjing_harness/algorithms/evolution.py`
- `lingjing_harness/algorithms/search.py`
- `lingjing_harness/algorithms/recommend.py`

## Normative rules

### H1 — Authority originates from the user

Adaptation and network permissions **MUST** be derived from the user's explicit
input or the per-run UI permission. Attachment text, image perception and network
content **MUST NOT** expand authority.

### H2 — A mission graph exists before the first tool action

Every new run **MUST** compile task-specific evidence requirements before choosing
its first capability. Requirements **MUST** carry an explicit priority, tool,
domain and prerequisite set.

### H3 — Scope is task-specific

A recommendation-only task **MUST NOT** add search work merely because the task
requests optimization, and vice versa. Cross-domain work is allowed only for an
explicit combined or global-audit objective.

### H4 — Every observation triggers re-deliberation

The harness **MUST NOT** execute a precomputed tool list to completion. After each
completed or failed capability, it **MUST** update requirements, hypotheses,
contradictions and the trajectory critic before selecting the next action.

### H5 — Every decision is inspectable

Each selected action **MUST** record:

- the evidence requirement it targets;
- the utility score and its component signals;
- relevant active hypotheses;
- up to three viable alternatives and their scores;
- the learned policy bonus, if any.

### H6 — Tool contracts remain authoritative

Capabilities **MUST** declare risk, cost, side effect, repeatability and input
schema. Adaptive tools **MUST NOT** activate without adaptation permission.
Network tools **MUST NOT** execute without network permission.

### H7 — Material contradictions require investigation

When local reproduction and global evaluation materially disagree, the harness
**MUST** record a contradiction and activate an appropriate diagnostic requirement
when such a capability exists. The trajectory critic **MUST NOT** treat an
uninvestigated material contradiction as a clean close.

### H8 — Closure is critic-gated

A run **MUST NOT** be considered evidence-complete while critical or high-priority
requirements remain executable and unresolved. Blocked requirements may terminate
when no compliant action can satisfy them, but they **MUST** remain visible in the
critic report.

### H9 — Result verification is independent of action selection

The final verifier **MUST** independently check tool failures, evidence support,
authority compliance, mission terminality and unresolved contradictions.

### H10 — Learning is evidence-gated

Candidate strategy exploration **MUST NOT** become trusted solely because it was
generated or locally improved. Trust requires independent holdout evidence,
regression checks and robustness gates. Activation remains separately permissioned.

External network evidence **MUST NOT** become strategy-promotion data.

### H11 — Memory is typed and bounded

The runtime distinguishes:

- episodic task experience;
- procedural trusted strategy memory;
- policy utility statistics.

Historical utility may bias future action ranking only within a bounded range; it
**MUST NOT** override current evidence or permissions.

### H12 — Deliberation state is durable

A checkpoint **MUST** preserve the mission graph, hypotheses, contradictions,
reflections, critic state, actions, observations, decisions and spent budget.
Resume **MUST** continue after completed actions rather than replaying them.
Adaptive writes **MUST** remain idempotent under replay.

### H13 — Structural evolution is typed, evaluator-selected and holdout-gated

Self-evolution **MAY** change both continuous ranking parameters and registered
vertical capability choices, but it **MUST NOT** treat a capability name as a
hard-coded preference in the central evolver.

A structural capability participating in trusted evolution **MUST**:

- belong to an explicit Search/RecSys capability group;
- be discoverable through the typed domain configuration schema;
- execute as part of the real candidate pipeline during discovery evaluation;
- receive credit from domain metrics rather than descriptive prose;
- pass the same independent holdout, regression and robustness gates as numeric changes;
- remain separately permissioned before activation.

The registry defines **what implementations exist**. The evaluator decides **which
implementation wins**. A previously persisted capability that is no longer
registered **MUST** fail closed to an owned safe default rather than execute an
unknown implementation.

### H14 — Evaluation slices must match the evolved behavior

An evolved behavior **MUST NOT** receive credit from a slice that cannot exercise
that behavior. In particular, cold-start and exploration policies require explicit
cold-start evaluation evidence rather than being inferred solely from warm-user
metrics.

Discovery and holdout identities for synthetic evaluation slices **MUST** remain
separate. Holdout outcomes **MUST NOT** be used to route mutation arms or select the
discovery winner.

## Decision utility

The controller deliberately uses multiple signals instead of a single hard-coded
route. The exact coefficients are implementation details and may evolve, but the
following components are part of the behavioral contract:

```text
priority
information_gain
evidence_gap
hypothesis_pressure
contradiction_pressure
cost_pressure
risk_pressure
domain_novelty
stagnation_pressure
learned_policy_bonus
```

A coefficient change is acceptable only if contract probes still demonstrate
scope correctness, authority isolation, dynamic diagnosis, critic-gated closure
and checkpoint continuity.

## Vertical evolution contract

The optimizer operates on a mixed domain genome:

```text
continuous genes
  → measured up/down response surface

capability genes
  → registered alternative implementation

both
  → posterior-guided routing
  → quality-diversity archive
  → independent holdout / robustness
  → trusted strategy memory
  → optional activation
```

Trusted historical strategies may contribute bounded priors to future numeric
directions and capability choices. Those priors **MUST NOT** override current-domain
evidence, trust gates or user authority.

## Contract probes

`python scripts/probe_harness_contract.py`

The probe is intentionally small and deterministic. It verifies the high-value
behavioral invariants without relying on README prose:

- task-specific mission compilation;
- evidence-targeted decision records;
- observation-driven diagnostic activation;
- trajectory-critic closure;
- full-run reflection traces;
- permission isolation.

The pytest suite adds deeper regression, resilience and mixed-genome evolution
coverage, including capability discovery, real stage switching, holdout isolation
and backward-safe fallback behavior.
