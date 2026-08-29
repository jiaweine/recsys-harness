from __future__ import annotations

from pathlib import Path
import re
import sys


PROJECT_DISTRIBUTION = "xushu-recsys-harness"
_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+)$")


def normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_constraints(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.fullmatch(line)
        if match is None:
            raise ValueError(f"{path}:{line_number}: runtime constraints must be exact == pins")
        name, version = match.groups()
        normalized = normalize_distribution(name)
        if normalized in pins:
            raise ValueError(f"{path}:{line_number}: duplicate runtime pin for {normalized}")
        pins[normalized] = version
    if not pins:
        raise ValueError(f"{path}: no runtime constraints found")
    return pins


def load_wheelhouse(path: Path) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for wheel in sorted(path.glob("*.whl")):
        parts = wheel.name.split("-")
        if len(parts) < 2:
            raise ValueError(f"unexpected wheel filename: {wheel.name}")
        name = normalize_distribution(parts[0])
        version = parts[1]
        if name in resolved:
            raise ValueError(f"multiple wheel versions resolved for {name}")
        resolved[name] = version
    if not resolved:
        raise ValueError(f"{path}: no wheels found")
    return resolved


def verify(constraints_path: Path, wheelhouse_path: Path) -> None:
    expected = load_constraints(constraints_path)
    resolved = load_wheelhouse(wheelhouse_path)
    project = normalize_distribution(PROJECT_DISTRIBUTION)
    if project not in resolved:
        raise ValueError(f"project wheel missing from {wheelhouse_path}")
    resolved.pop(project)

    missing = sorted(set(expected) - set(resolved))
    unlocked = sorted(set(resolved) - set(expected))
    mismatched = sorted(
        name
        for name in set(expected) & set(resolved)
        if expected[name] != resolved[name]
    )
    if missing or unlocked or mismatched:
        details = []
        if missing:
            details.append(f"missing wheels for locked packages: {missing}")
        if unlocked:
            details.append(f"resolved packages without pins: {unlocked}")
        if mismatched:
            details.append(
                "version mismatches: "
                + ", ".join(
                    f"{name} locked={expected[name]} resolved={resolved[name]}"
                    for name in mismatched
                )
            )
        raise ValueError("; ".join(details))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_runtime_wheelhouse.py CONSTRAINTS WHEELHOUSE")
    try:
        verify(Path(sys.argv[1]), Path(sys.argv[2]))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
