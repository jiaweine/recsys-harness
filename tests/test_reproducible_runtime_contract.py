from pathlib import Path
import re
import tomllib

import pytest

from scripts.verify_runtime_wheelhouse import load_constraints, normalize_distribution, verify


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CONSTRAINTS = ROOT / "requirements-runtime.txt"


def _project_runtime_dependencies() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = set()
    for dependency in project["dependencies"]:
        match = re.match(r"([A-Za-z0-9_.-]+)", dependency)
        assert match is not None, f"could not parse project dependency: {dependency}"
        names.add(normalize_distribution(match.group(1)))
    return names


def test_runtime_constraints_are_exact_and_cover_project_dependencies() -> None:
    pins = load_constraints(RUNTIME_CONSTRAINTS)
    missing_direct = sorted(_project_runtime_dependencies() - pins.keys())
    assert not missing_direct, f"runtime lock is missing direct dependencies: {missing_direct}"

    forbidden = {
        "pytest",
        "httpx",
        "optuna",
        "implicit",
        "flagembedding",
    }
    leaked = sorted(forbidden & pins.keys())
    assert not leaked, f"dev/optional dependencies leaked into runtime lock: {leaked}"


def test_ci_and_container_consume_the_same_runtime_constraints() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "-c requirements-runtime.txt" in requirements
    assert "--constraint requirements-runtime.txt" in dockerfile
    assert "verify_runtime_wheelhouse.py requirements-runtime.txt /wheels" in dockerfile


def test_container_base_image_is_immutable_and_shared_by_both_stages() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 2

    image_refs = [line.split()[1] for line in from_lines]
    assert image_refs[0] == image_refs[1]
    assert re.fullmatch(r"python:3\.11-slim@sha256:[0-9a-f]{64}", image_refs[0])


def test_runtime_wheelhouse_verifier_rejects_unlocked_or_mismatched_wheels(tmp_path: Path) -> None:
    constraints = tmp_path / "runtime.txt"
    constraints.write_text("alpha==1.0\nbeta==2.0\n", encoding="utf-8")
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()

    for name in ("alpha-1.0-py3-none-any.whl", "beta-2.0-py3-none-any.whl", "xushu_recsys_harness-0.1.0-py3-none-any.whl"):
        (wheelhouse / name).touch()

    verify(constraints, wheelhouse)

    rogue = wheelhouse / "rogue-3.0-py3-none-any.whl"
    rogue.touch()
    with pytest.raises(ValueError, match="without pins"):
        verify(constraints, wheelhouse)
    rogue.unlink()

    (wheelhouse / "beta-2.0-py3-none-any.whl").unlink()
    (wheelhouse / "beta-2.1-py3-none-any.whl").touch()
    with pytest.raises(ValueError, match="version mismatches"):
        verify(constraints, wheelhouse)


def test_dependabot_maintains_actions_python_and_container_dependencies() -> None:
    dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    for ecosystem in ("github-actions", "pip", "docker"):
        assert f'package-ecosystem: "{ecosystem}"' in dependabot
