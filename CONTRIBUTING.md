# Contributing to Xushu · Recsys Harness

Thanks for improving Xushu. The project favors changes that are easy to verify, easy to review, and explicit about the evidence behind them.

## Development setup

```bash
git clone https://github.com/jiaweine/recsys-harness.git
cd recsys-harness
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run the standard checks before opening a pull request:

```bash
make check
make test
python scripts/probe_harness_contract.py
```

For product-facing changes, also run:

```bash
make demo
```

## What makes a strong change

A good contribution makes the intent and verification path obvious:

- explain the user or system outcome the change improves;
- keep Search / Recommendation behavior deterministic where the contract requires it;
- preserve authority, evidence, recovery, and lifecycle semantics;
- add or update regression coverage for behavior changes;
- keep business reward separate from domain guardrails;
- update docs when a public contract, command, configuration, or data shape changes.

## Pull requests

Keep each pull request focused enough that a reviewer can answer:

1. What changed?
2. Why is the change useful?
3. What evidence shows it works?
4. Which contracts or invariants does it touch?

Screenshots are useful for UI changes. Test output, replay evidence, or before/after metrics are useful for algorithm and runtime changes.

## Project structure

- `lingjing_harness/runtime/` — Agent Harness, mission state, tools, verification, memory
- `lingjing_harness/algorithms/` — Search / Recommendation evaluation and evolution
- `lingjing_harness/production.py` — RewardSpec, ExposureEvent, replay and temporal validation
- `frontend/` — product UI
- `docs/` — architecture, contracts, data formats and acceptance criteria
- `tests/` — regression coverage

For architecture-sensitive changes, read `docs/ARCHITECTURE.md` and `docs/HARNESS_CONTRACT.md` first.
