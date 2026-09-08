from __future__ import annotations

import argparse
import re
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.+-]*)?$")


class ReleaseContractError(ValueError):
    """The repository cannot be released under the requested tag."""


def project_version(root: Path = ROOT) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        version = str(data["project"]["version"]).strip()
    except (KeyError, TypeError) as exc:
        raise ReleaseContractError("pyproject.toml must declare project.version") from exc
    if not _VERSION.fullmatch(version):
        raise ReleaseContractError(f"unsupported release version: {version!r}")
    return version


def expected_tag(version: str) -> str:
    return f"v{version}"


def normalize_tag(tag: str) -> str:
    value = str(tag or "").strip()
    prefix = "refs/tags/"
    if value.startswith(prefix):
        value = value[len(prefix) :]
    return value


def changelog_has_release(root: Path, version: str) -> bool:
    path = root / "CHANGELOG.md"
    if not path.is_file():
        return False
    heading = re.compile(rf"^##\s+\[{re.escape(version)}\](?:\s|$)", re.MULTILINE)
    return bool(heading.search(path.read_text(encoding="utf-8")))


def validate_release_contract(root: Path = ROOT, *, tag: str | None = None) -> str:
    root = Path(root)
    version = project_version(root)
    if not changelog_has_release(root, version):
        raise ReleaseContractError(
            f"CHANGELOG.md must contain a '## [{version}]' release section"
        )
    if tag is not None:
        actual = normalize_tag(tag)
        wanted = expected_tag(version)
        if actual != wanted:
            raise ReleaseContractError(
                f"release tag {actual!r} does not match project version {version!r}; expected {wanted!r}"
            )
    return version


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Xushu package version, changelog, and optional release tag."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tag", help="Release tag, for example v0.1.0 or refs/tags/v0.1.0")
    args = parser.parse_args()
    version = validate_release_contract(args.root, tag=args.tag)
    print(f"release contract ok: version={version} tag={expected_tag(version)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
