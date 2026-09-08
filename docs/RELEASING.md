# Releasing Xushu

The release source of truth is `project.version` in `pyproject.toml`. A public GitHub release is created only from an immutable tag whose name exactly matches that version: `v<version>`.

## Release contract

A releasable commit must satisfy all of the following:

1. `python scripts/check_release_contract.py` passes;
2. `CHANGELOG.md` contains a `## [<version>]` section;
3. the standard acceptance suite is green on the release commit;
4. wheel and source distribution both build successfully;
5. both distributions contain the runtime package and real frontend product;
6. both distributions can be installed cleanly and serve `/`, `/health/live`, `/health/ready`, and `/assets/app.js`;
7. release assets include portable SHA-256 checksums that verify after the workflow-artifact handoff.

The tag-triggered `.github/workflows/release.yml` enforces the same contract before it receives any permission to publish a release.

## Dry-run the release pipeline

The Release workflow supports `workflow_dispatch`. Release-infrastructure pull requests also run the same build-and-verify job automatically. A PR or manual run uploads the verified bundle as a workflow artifact, but it **does not create a GitHub Release** because no immutable release tag is present.

Locally, the equivalent core checks are:

```bash
python -m pip install -r requirements.txt
python scripts/check_release_contract.py
python -m compileall -q lingjing_harness tests scripts
pytest -q
python scripts/demo_smoke.py > /dev/null
python scripts/probe_harness_contract.py > /dev/null
python -m build --sdist --wheel --outdir dist
python scripts/verify_release_artifacts.py dist
(cd dist && sha256sum *.whl *.tar.gz > SHA256SUMS)
(cd dist && sha256sum -c SHA256SUMS)
```

## Publish a release

After the intended commit is on `main` and its required checks are green:

```bash
VERSION="$(python - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])
PY
)"
python scripts/check_release_contract.py --tag "v${VERSION}"
git tag "v${VERSION}"
git push origin "v${VERSION}"
```

The tag starts the Release workflow. Its read-only build job runs the release acceptance suite, builds the wheel and source distribution, verifies clean installs, generates `SHA256SUMS`, verifies those checksums, and uploads the bundle. The publish job downloads that exact bundle and verifies `SHA256SUMS` again before receiving the release assets as publish inputs. Only after the build job succeeds does this separate job receive `contents: write`; it executes no repository code and creates the immutable GitHub Release from the already-verified artifacts.

Do not move or reuse a published release tag. If a released artifact needs a code change, increment `project.version`, add the matching changelog section, and create a new tag.

## Package-index publishing

The repository does not publish to PyPI automatically. If package-index distribution is added later, prefer a trusted-publishing/OIDC flow and make it consume the same verified artifacts rather than rebuilding under a separate write-capable job.

## Licensing

Release automation deliberately does not infer or choose a software license. The repository owner must explicitly select and add any license intended to grant third parties redistribution or modification rights; that legal choice should also be reflected in package metadata before a license-bearing release is published.
