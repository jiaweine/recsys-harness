from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check_workspace_switcher.py"


def test_workspace_switcher_waits_for_final_history_hydration() -> None:
    source = CHECK.read_text(encoding="utf-8")

    ready = 'page.locator("body.ready").wait_for(state="attached", timeout=15_000)'
    expected_current = 'expected_current = page.locator('
    exact_search = 'f\'.history-item[aria-current="page"][data-id="{search["id"]}"]\''
    unique_current = 'expect(current).to_have_count(1, timeout=10_000)'

    assert ready in source, "Workspace Switcher QA must wait for app boot readiness"
    assert expected_current in source and exact_search in source, (
        "Workspace Switcher QA must wait for the newest persisted task to become current"
    )
    assert unique_current in source, (
        "Workspace Switcher QA must use Playwright auto-retry for the unique current marker"
    )

    ready_at = source.index(ready)
    expected_at = source.index(expected_current)
    unique_at = source.index(unique_current)
    assert ready_at < expected_at < unique_at, (
        "Workspace Switcher QA must establish boot readiness and target convergence before "
        "asserting the global current-count invariant"
    )


def test_workspace_switcher_does_not_use_fixed_sleep_for_initial_hydration() -> None:
    source = CHECK.read_text(encoding="utf-8")
    start = source.index('page.goto(BASE_URL')
    palette = source.index('conversation_requests.clear()')
    initial_hydration = source[start:palette]

    assert "wait_for_timeout" not in initial_hydration, (
        "Initial Workspace Switcher hydration must use observable readiness, not a fixed sleep"
    )
