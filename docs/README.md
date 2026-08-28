# Xushu Documentation

This directory contains the technical contracts behind **Xushu · Recsys Harness**. The root `README.md` explains the product and quick start; the documents here go deeper into runtime behavior, data, evaluation, evolution, experimentation, operations, and acceptance criteria.

## Start here

| Document | Use it for |
| --- | --- |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System boundaries, runtime planes, evidence flow, strategy lifecycle, persistence and production value architecture. |
| [`HARNESS_CONTRACT.md`](HARNESS_CONTRACT.md) | Mission Graph, deliberation, tools, evidence requirements, authority, verification and recovery semantics. |
| [`DATA_FORMAT.md`](DATA_FORMAT.md) | Catalog, interaction, production event, RewardSpec and replay data contracts. |
| [`COUNTERFACTUAL_EXPERIMENTS.md`](COUNTERFACTUAL_EXPERIMENTS.md) | Explicit IPS / SNIPS / DR off-policy evaluation, overlap diagnostics and controlled-experiment eligibility gates. |
| [`VERTICAL_EVOLUTION.md`](VERTICAL_EVOLUTION.md) | Search / Recommendation strategy genome, evaluation, response surfaces, routing, holdout and trust flow. |
| [`OPERATIONS.md`](OPERATIONS.md) | Production liveness/readiness semantics, workspace convergence and container probe behavior. |
| [`ACCEPTANCE.md`](ACCEPTANCE.md) | Verifiable acceptance criteria for runtime, product, evaluation, recovery and integration behavior. |
| [`DESIGN.md`](DESIGN.md) | Product and interaction design principles for the task-first workspace. |

## Reading paths

### I want to integrate my existing Search / Recommendation system

1. Read [`DATA_FORMAT.md`](DATA_FORMAT.md) for input and production evidence contracts.
2. Read the integration sections in [`ARCHITECTURE.md`](ARCHITECTURE.md).
3. Use the serving adapters described in the root [`README.md`](../README.md).
4. If the integration can emit explicit logging and target action probabilities, add [`COUNTERFACTUAL_EXPERIMENTS.md`](COUNTERFACTUAL_EXPERIMENTS.md) to the evaluation path.
5. Validate the integration against [`ACCEPTANCE.md`](ACCEPTANCE.md).

### I want to understand the Agent Harness

1. Start with [`HARNESS_CONTRACT.md`](HARNESS_CONTRACT.md).
2. Follow the runtime and trust planes in [`ARCHITECTURE.md`](ARCHITECTURE.md).
3. Inspect `lingjing_harness/runtime/` for the implementation surface.

### I want to operate Xushu in production

1. Read [`OPERATIONS.md`](OPERATIONS.md) for liveness, readiness and workspace-convergence semantics.
2. Use `/health/live` for process liveness and `/health/ready` for traffic readiness.
3. Keep `/api/status` behind the product authentication boundary rather than reusing it as an infrastructure probe.
4. Use [`ARCHITECTURE.md`](ARCHITECTURE.md) when reasoning about multi-worker durable state and recovery.

### I want to evaluate a candidate policy with explicit counterfactual evidence

1. Read [`COUNTERFACTUAL_EXPERIMENTS.md`](COUNTERFACTUAL_EXPERIMENTS.md).
2. Supply `CounterfactualRecord` rows with logging and target propensities for the same logged action.
3. Use IPS / SNIPS or DR plus overlap diagnostics and bootstrap confidence.
4. Apply `ExperimentCriteria` when deciding whether evidence is sufficient for a controlled online test.

### I want to understand strategy evolution

1. Read [`VERTICAL_EVOLUTION.md`](VERTICAL_EVOLUTION.md).
2. Use [`DATA_FORMAT.md`](DATA_FORMAT.md) to understand business reward and production events.
3. Use [`COUNTERFACTUAL_EXPERIMENTS.md`](COUNTERFACTUAL_EXPERIMENTS.md) for explicit policy-probability OPE and experiment eligibility.
4. Use [`ACCEPTANCE.md`](ACCEPTANCE.md) to see the evidence and regression gates expected of a strategy lifecycle.

### I want to change the product UI

1. Read [`DESIGN.md`](DESIGN.md).
2. Inspect `frontend/` and the real-browser README screenshots.
3. Run the product/browser checks described in the root [`README.md`](../README.md) before submitting a change.

## Core principle

The documents describe one system contract from different angles:

```text
Goal + Authority
      ↓
Mission + Evidence
      ↓
Real Search / Recommendation execution
      ↓
Business Reward + Domain Guardrails
      ↓
Replay / Counterfactual Evaluation
      ↓
Temporal Holdout + Confidence
      ↓
Controlled Experiment Eligibility
      ↓
Trusted Strategy Memory
      ↓
Permissioned Activation
      ↓
Revalidation / Retirement
```

The runtime, evaluation, data, experimentation, operations, and UI layers should preserve that evidence chain rather than creating separate sources of truth.
