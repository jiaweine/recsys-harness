from __future__ import annotations

import argparse
from pathlib import Path
import tarfile
import tomllib
import zipfile


PROJECT_NAME = "xushu-recsys-harness"
DIST_NAME = "xushu_recsys_harness"


def project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        return str(data["project"]["version"]).strip()
    except (KeyError, TypeError) as exc:
        raise ValueError("pyproject.toml must declare project.version") from exc


def _metadata_value(text: str, key: str) -> str | None:
    prefix = f"{key}: "
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def verify_release_artifacts(dist: Path, root: Path) -> tuple[Path, Path]:
    version = project_version(root)
    wheels = sorted(dist.glob(f"{DIST_NAME}-{version}-*.whl"))
    sdists = sorted(dist.glob(f"{DIST_NAME}-{version}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(
            f"expected exactly one wheel and one sdist for {version}; "
            f"found wheels={wheels}, sdists={sdists}"
        )
    wheel, sdist = wheels[0], sdists[0]

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError(f"wheel metadata is ambiguous: {metadata_names}")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
        required = {
            "lingjing_harness/__init__.py",
            "lingjing_harness/api.py",
            "frontend/__init__.py",
            "frontend/index.html",
            "frontend/app.js",
        }
        missing = sorted(required - names)
        if missing:
            raise ValueError(f"wheel is missing runtime product files: {missing}")
        forbidden = [
            name
            for name in names
            if name.startswith(("tests/", "scripts/", "data/", "build/", "dist/"))
            or ".egg-info/" in name
        ]
        if forbidden:
            raise ValueError(f"wheel contains repository-only files: {forbidden[:10]}")
        if _metadata_value(metadata, "Name") != PROJECT_NAME:
            raise ValueError("wheel project name does not match pyproject.toml contract")
        if _metadata_value(metadata, "Version") != version:
            raise ValueError("wheel version does not match pyproject.toml contract")

    prefix = f"{DIST_NAME}-{version}/"
    with tarfile.open(sdist, mode="r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
        required = {
            f"{prefix}pyproject.toml",
            f"{prefix}README.md",
            f"{prefix}lingjing_harness/__init__.py",
            f"{prefix}lingjing_harness/api.py",
            f"{prefix}frontend/__init__.py",
            f"{prefix}frontend/index.html",
            f"{prefix}frontend/app.js",
        }
        missing = sorted(required - names)
        if missing:
            raise ValueError(f"sdist is missing source/product files: {missing}")
        pkg_info = f"{prefix}{DIST_NAME}.egg-info/PKG-INFO"
        if pkg_info not in names:
            alternatives = [name for name in names if name.endswith("/PKG-INFO")]
            if len(alternatives) != 1:
                raise ValueError(f"sdist metadata is ambiguous: {alternatives}")
            pkg_info = alternatives[0]
        extracted = archive.extractfile(pkg_info)
        if extracted is None:
            raise ValueError("sdist PKG-INFO could not be read")
        metadata = extracted.read().decode("utf-8")
        if _metadata_value(metadata, "Name") != PROJECT_NAME:
            raise ValueError("sdist project name does not match pyproject.toml contract")
        if _metadata_value(metadata, "Version") != version:
            raise ValueError("sdist version does not match pyproject.toml contract")

    return wheel, sdist


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Xushu wheel and sdist release contents.")
    parser.add_argument("dist", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    wheel, sdist = verify_release_artifacts(args.dist, args.root)
    print(f"release artifacts ok: wheel={wheel.name} sdist={sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
