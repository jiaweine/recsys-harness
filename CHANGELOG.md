# Changelog

All notable user-visible and contract-level changes are recorded here. The project uses the version declared in `pyproject.toml` as the release source of truth.

## [0.1.0]

Initial public release of **Xushu · Recsys Harness**.

### Product

- Task-first Search / Recommendation workspace with durable conversations, run progress, evidence, attachments, workspace switching, and desktop/mobile browser flows.
- Text, JSON, CSV, Markdown, TXT, and image attachments with bounded storage, local document parsing, and optional image perception.
- Explicit per-run network authority and production authentication/rate-limit/CSP boundaries.

### Agent harness

- Mission Graph execution with observation, deliberation, tool selection, reflection, checkpointing, replanning, verification, and bounded memory.
- Typed tool risk/cost/side-effect contracts, adaptive invocation idempotency, and evidence-linked final results.
- Durable checkpoint recovery, session-scoped worker leases, heartbeat recovery, fencing, graceful handoff, remote cancellation, and stale-executor protection.

### Search / recommendation evaluation

- Search and recommendation reference engines plus adapters for existing serving systems.
- Business `RewardSpec`, production replay, domain guardrails, temporal holdout, paired confidence, and strategy lifecycle gates.
- Explicit IPS / SNIPS / doubly robust counterfactual evaluation with overlap diagnostics and controlled-experiment eligibility criteria.
- Mixed continuous/capability strategy genome, response-surface routing, posterior-guided exploration, and QD archive support.

### Operations and quality

- SQLite-backed durable runtime and workspace publication protocol.
- `/health/live` and `/health/ready` production probes.
- Wheel-contained web product, container QA, browser visual QA, accessibility/theme checks, algorithm quality suites, and durable/system stress workflows.

[0.1.0]: https://github.com/jiaweine/recsys-harness/releases/tag/v0.1.0
