# Xushu Documentation

This directory contains the technical contracts behind **Xushu · Recsys Harness**. The root `README.md` explains the product and quick start; the documents here go deeper into runtime behavior, data, evaluation, evolution, and acceptance criteria.

## Start here

| Document | Use it for |
| --- | --- |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System boundaries, runtime planes, evidence flow, strategy lifecycle, persistence and production value architecture. |
| [`HARNESS_CONTRACT.md`](HARNESS_CONTRACT.md) | Mission Graph, deliberation, tools, evidence requirements, authority, verification and recovery semantics. |
| [`DATA_FORMAT.md`](DATA_FORMAT.md) | Catalog, interaction, production event, RewardSpec and replay data contracts. |
| [`VERTICAL_EVOLUTION.md`](VERTICAL_EVOLUTION.md) | Search / Recommendation strategy genome, evaluation, response surfaces, routing, holdout and trust flow. |
| [`ACCEPTANCE.md`](ACCEPTANCE.md) | Verifiable acceptance criteria for runtime, product, evaluation, recovery and integration behavior. |
| [`DESIGN.md`](DESIGN.md) | Product and interaction design principles for the task-first workspace. |

## Reading paths

### I want to integrate my existing Search / Recommendation system

1. Read [`DATA_FORMAT.md`](DATA_FORMAT.md) for input and production evidence contracts.
2. Read the integration sections in [`ARCHITECTURE.md`](ARCHITECTURE.md).
3. Use the serving adapters described in the root [`README.md`](../README.md).
4. Validate the integration against [`ACCEPTANCE.md`](ACCEPTANCE.md).

### I want to understand the Agent Harness

1. Start with [`HARNESS_CONTRACT.md`](HARNESS_CONTRACT.md).
2. Follow the runtime and trust planes in [`ARCHITECTURE.md`](ARCHITECTURE.md).
3. Inspect `lingjing_harness/runtime/` for the implementation surface.

### I want to understand strategy evolution

1. Read [`VERTICAL_EVOLUTION.md`](VERTICAL_EVOLUTION.md).
2. Use [`DATA_FORMAT.md`](DATA_FORMAT.md) to understand business reward and production events.
3. Use [`ACCEPTANCE.md`](ACCEPTANCE.md) to see the evidence and regression gates expected of a strategy lifecycle.

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
Temporal Holdout + Confidence
      ↓
Trusted Strategy Memory
      ↓
Permissioned Activation
      ↓
Revalidation / Retirement
```

The runtime, evaluation, data, and UI layers should preserve that evidence chain rather than creating separate sources of truth.
