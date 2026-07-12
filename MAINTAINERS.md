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
