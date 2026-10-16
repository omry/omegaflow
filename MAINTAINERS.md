# OmegaFlow Maintainer Guide

This guide covers repository development and validation. For installation and
product usage, see the [README](README.md) and
[documentation](https://omegaflow.dev/getting-started/).

## Python development

Install OmegaFlow and its development dependencies from the checkout:

```bash
python -m pip install -e '.[dev]'
```

## Website development

Use Node 22, at least 22.13.0, as declared in `website/.nvmrc`.
The website pins pnpm 11.21.0 in `website/package.json`. Enable Corepack
with your Node installation:

```bash
corepack enable
```

Install the website dependencies once per checkout, and again when the lockfile
changes:

```bash
cd website
corepack pnpm install --frozen-lockfile
```

Start the local development server whenever you work on the website:

```bash
corepack pnpm start
```

## Validation

From the repository root, run these common checks before submitting a change.
The website check verifies generated schema docs before the production build:

```bash
nox -s tests
cd website
corepack pnpm run check
```

## Website dependency maintenance

[Shared Docusaurus workflows](https://github.com/hydra-ecosystem/github-actions/blob/main/docs/docusaurus.md)
audit the standalone `website` project and validate proposals with its `check`
script. This preserves the schema checks and production build. The configuration
in `.github/docusaurus.json` follows the project's Node and pnpm declarations.
It enables security fixes and routine upgrades, including major versions.

The audit runs on January 30 at 02:00 UTC. Dispatch **Docusaurus dependency audit**
from `main` in GitHub Actions for an earlier run. Change a schedule in both the
JSON configuration and `.github/workflows/dependency-audit.yml`.

Before the first run, enable **Allow GitHub Actions to create and approve pull
requests** under **Settings → Actions → General → Workflow permissions**.
A maintainer may need to approve workflows on PRs created with the default
`GITHUB_TOKEN`. Maintenance does not deploy the website.

The workspace applies a ten-day release delay and explicit dependency build
script approvals. It retains the existing security overrides and the `core-js`
build approval. Review any additional approvals a dependency update needs.

## Release procedure

Releases are prepared on `main` and published only after the release candidate
has passed CI and a maintainer has explicitly approved publication.

1. Choose a version greater than the latest version on PyPI. Update
   `project.version` in `pyproject.toml`, `__version__` in
   `src/omegaflow/__init__.py`, and its assertion in
   `tests/test_studio_cli.py` to the same version.
2. Add release fragments under `changes/`, then preview the notes:

   ```bash
   nox -s release_notes -- VERSION
   ```

3. Generate `CHANGELOG.md`, review the rendered entry, and commit the release
   preparation:

   ```bash
   towncrier build --version VERSION
   ```

4. Run the release-candidate checks. Rebuild and inspect any recording whose
   source or inputs changed; the command shown here is the homepage demo:

   ```bash
   nox -s ci
   nox -s package
   nox -s website
   omegaflow recording=quickstart-demo action=build
   omegaflow recording=quickstart-demo action=check
   ```

5. Push the release-preparation commit and wait for every required `main` check
   to pass. Review the package artifacts, changelog, public documentation, and
   affected recordings. Stop here until a maintainer explicitly approves the
   tag and external publication.

   For the initial `0.9.0` release only, replace the complete repository
   history with one reviewed release-root commit after the candidate tree is
   final. Remove all consumed news fragments, rerun the complete local release
   checks against that root, obtain explicit approval for the destructive
   force-push, force-push `main`, and wait for the replacement commit's required
   CI checks before creating the tag.
6. Create and push an annotated tag whose name is exactly `vVERSION`:

   ```bash
   git tag -a vVERSION -m "OmegaFlow VERSION"
   git push origin vVERSION
   ```

   Pushing the tag starts `.github/workflows/publish.yml`. The workflow rejects
   a tag, package version, or changelog mismatch and stops if that version is
   already present on PyPI.
7. If the tag workflow must be started manually, select the existing
   `vVERSION` tag in the GitHub Actions ref selector and enter the same exact
   `VERSION` input. Do not run it from `main`.
8. Verify the version and expected files on PyPI, then verify that the GitHub
   Release contains the reviewed changelog entry, one sdist, and all four
   platform wheels. Record the outcome in the release checklist and backlog.

The publish workflow builds the distributions once. PyPI trusted publishing
and the GitHub Release consume the same uploaded artifacts; only their jobs
receive the write permissions they require.
