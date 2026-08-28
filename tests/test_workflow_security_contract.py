from pathlib import Path
import re


WORKFLOW_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
OFFICIAL_ACTIONS = {
    "actions/checkout",
    "actions/setup-python",
    "actions/upload-artifact",
    "actions/download-artifact",
}
ACTION_PATTERN = re.compile(r"uses:\s*(actions/[A-Za-z0-9_-]+)@([^\s#]+)")
IMMUTABLE_SHA = re.compile(r"^[0-9a-f]{40}$")


def _workflow_paths() -> list[Path]:
    return sorted((*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")))


def _top_level_permissions(text: str) -> list[str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line == "permissions:":
            block: list[str] = []
            for nested in lines[index + 1 :]:
                if nested and not nested.startswith((" ", "\t")):
                    break
                block.append(nested.strip())
            return block
    return []


def test_all_workflows_default_to_read_only_contents() -> None:
    missing = []
    for path in _workflow_paths():
        permissions = _top_level_permissions(path.read_text(encoding="utf-8"))
        if "contents: read" not in permissions:
            missing.append(path.name)
    assert not missing, f"workflows must default to contents: read: {missing}"


def test_official_actions_are_pinned_to_immutable_commits() -> None:
    violations = []
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        for action, revision in ACTION_PATTERN.findall(text):
            if action in OFFICIAL_ACTIONS and not IMMUTABLE_SHA.fullmatch(revision):
                violations.append(f"{path.name}: {action}@{revision}")
    assert not violations, "official GitHub Actions must use 40-character commit SHAs: " + "; ".join(violations)


def test_workflow_contract_covers_every_official_action_family_in_use() -> None:
    used = {
        action
        for path in _workflow_paths()
        for action, _revision in ACTION_PATTERN.findall(path.read_text(encoding="utf-8"))
    }
    uncovered = sorted(used - OFFICIAL_ACTIONS)
    assert not uncovered, f"official action families need an explicit pin policy: {uncovered}"
