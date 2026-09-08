from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "system-stress-hardening.yml"


CRITICAL_DURABLE_TESTS = (
    "tests/test_run_lease_execution_fence.py",
    "tests/test_run_recovery_claim_clock.py",
    "tests/test_cancel_side_effect_fence.py",
    "tests/test_terminal_takeover_execution_fence.py",
    "tests/test_assistant_publish_cancel_linearization.py",
    "tests/test_assistant_publish_terminal_takeover.py",
    "tests/test_run_completion_linearization_stress.py",
    "tests/test_startup_recovery_batching.py",
    "tests/test_store_fencing_stress.py",
)


def test_system_stress_triggers_on_all_api_and_store_boundary_modules() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    # Keep the durable stress workflow future-proof: new API/store boundary
    # modules must automatically trigger it instead of relying on a manually
    # maintained list that can silently miss a newly introduced fence.
    assert workflow.count("'lingjing_harness/api*.py'") == 2
    assert workflow.count("'lingjing_harness/store*.py'") == 2
    assert "workflow_dispatch:" in workflow


def test_system_stress_repeats_critical_durable_race_contracts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for test_path in CRITICAL_DURABLE_TESTS:
        # Each critical contract appears in both push/pull_request path filters
        # and in the repeated pytest command, so any test change triggers the
        # heavy suite and the suite actually exercises the changed boundary.
        assert workflow.count(test_path) >= 3, test_path

    assert "for pass in 1 2 3; do" in workflow
