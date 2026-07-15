# OmegaFlow Maintainer Guide

This guide covers repository development and validation. For installation and
product usage, see the [README](README.md) and
[documentation](https://omegaflow.dev/docs/intro).

## Python development

Install OmegaFlow and its development dependencies from the checkout:

```bash
python -m pip install -e '.[dev]'
```

## Website development

Install `pnpm` once per machine if needed:

```bash
npm install --global pnpm@10.14.0
```

Install the website dependencies once per checkout, and again when the lockfile
changes:

```bash
pnpm --dir website install
```

Start the local development server whenever you work on the website:

```bash
pnpm --dir website start
```

## Validation

Run these common checks before submitting a change:

```bash
nox -s tests
pnpm --dir website build
```

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
