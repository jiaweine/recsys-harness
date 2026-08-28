from pathlib import Path
import re


WORKFLOW_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
EXPECTED_ACTION_MAJORS = {
    "actions/checkout": "v7",
    "actions/setup-python": "v7",
    "actions/upload-artifact": "v7",
    "actions/download-artifact": "v8",
}


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


def test_official_actions_use_supported_node24_runtime_majors() -> None:
    mismatches = []
    pattern = re.compile(r"uses:\s*(actions/[A-Za-z0-9_-]+)@([^\s#]+)")
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        for action, version in pattern.findall(text):
            expected = EXPECTED_ACTION_MAJORS.get(action)
            if expected is not None and version != expected:
                mismatches.append(f"{path.name}: {action}@{version} != {expected}")
    assert not mismatches, "unsupported GitHub Actions runtime refs: " + "; ".join(mismatches)


def test_workflow_contract_covers_every_official_action_family_in_use() -> None:
    pattern = re.compile(r"uses:\s*(actions/[A-Za-z0-9_-]+)@([^\s#]+)")
    used = {
        action
        for path in _workflow_paths()
        for action, _version in pattern.findall(path.read_text(encoding="utf-8"))
    }
    uncovered = sorted(used - EXPECTED_ACTION_MAJORS.keys())
    assert not uncovered, f"official action families need an explicit runtime policy: {uncovered}"
