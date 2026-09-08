from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_release_contract import (
    ReleaseContractError,
    expected_tag,
    project_version,
    validate_release_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_minimal_release_repo(root: Path, *, version: str, changelog: str) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "release-fixture"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


def test_repository_release_contract_matches_declared_version():
    version = project_version(ROOT)
    assert validate_release_contract(ROOT) == version
    assert validate_release_contract(ROOT, tag=expected_tag(version)) == version
    assert validate_release_contract(ROOT, tag=f"refs/tags/{expected_tag(version)}") == version


def test_release_contract_rejects_tag_version_mismatch(tmp_path):
    release_version = "1.2.3"
    mismatched_version = "1.2.4"
    _write_minimal_release_repo(
        tmp_path,
        version=release_version,
        changelog=f"# Changelog\n\n## [{release_version}]\n\nReady.\n",
    )
    with pytest.raises(ReleaseContractError, match="does not match project version"):
        validate_release_contract(tmp_path, tag="v" + mismatched_version)


def test_release_contract_requires_matching_changelog_section(tmp_path):
    release_version = "1.2.3"
    old_version = "1.2.2"
    _write_minimal_release_repo(
        tmp_path,
        version=release_version,
        changelog=f"# Changelog\n\n## [{old_version}]\n\nOld release.\n",
    )
    with pytest.raises(ReleaseContractError, match="CHANGELOG.md"):
        validate_release_contract(tmp_path, tag="v" + release_version)


def test_release_workflow_keeps_write_token_out_of_build_job():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" in workflow
    assert 'tags:\n      - "v*"' in workflow
    assert '".github/workflows/release.yml"' in workflow
    assert '"scripts/check_release_contract.py"' in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "publish:" in workflow
    assert "contents: write" in workflow
    assert "needs: build-and-verify" in workflow
    assert "github.event_name == 'push'" in workflow
    assert "refs/tags/v" in workflow
    assert "gh release create" in workflow
    assert "--verify-tag" in workflow
    assert "python scripts/check_release_contract.py --tag" in workflow
    assert "python scripts/verify_release_artifacts.py dist" in workflow
    assert "sha256sum *.whl *.tar.gz > SHA256SUMS" in workflow
    assert workflow.count("sha256sum -c SHA256SUMS") >= 2

    # A release artifact is not accepted merely because metadata exists: both
    # formats must install into clean virtual environments and expose the real CLI.
    assert "python -m venv /tmp/xushu-release-wheel" in workflow
    assert "python -m venv /tmp/xushu-release-sdist" in workflow
    assert '"$envdir/bin/python" -m pip install -r requirements-runtime.txt' in workflow
    assert '"$envdir/bin/xushu-harness"' in workflow

    # The clean-install web smoke must use only production runtime dependencies.
    # In particular it starts the installed Uvicorn server and probes it with the
    # standard library rather than pulling a TestClient-only dependency into runtime.
    assert "fastapi.testclient" not in workflow
    assert '"$envdir/bin/python" -m uvicorn lingjing_harness.api:app' in workflow
    assert "from urllib.request import urlopen" in workflow

    # All reusable actions are immutable SHA pins, matching the repository's
    # existing supply-chain policy for workflows that execute repository code.
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in workflow
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in workflow
