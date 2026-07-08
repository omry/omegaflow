# OmegaFlow

|  | Description |
| --- | --- |
| Project | [![PyPI version](https://badge.fury.io/py/omegaflow.svg)](https://badge.fury.io/py/omegaflow) [![Downloads](https://pepy.tech/badge/omegaflow/month)](https://pepy.tech/project/omegaflow) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License](https://img.shields.io/pypi/l/omegaflow.svg) |
| Code quality | [![CI](https://github.com/omry/omegaflow/actions/workflows/ci.yml/badge.svg)](https://github.com/omry/omegaflow/actions/workflows/ci.yml) |
| Docs and support | [![Docs](https://img.shields.io/badge/docs-omegaflow.dev-6eb6ff)](https://omegaflow.dev/) [![Zulip chat](https://img.shields.io/badge/chat-OmegaFlow%20channel-2e77d0?logo=zulip)](https://hydra-framework.zulipchat.com/#narrow/channel/omegaflow) |

**Website and docs: [omegaflow.dev](https://omegaflow.dev/)**

OmegaFlow turns scripted terminal workflows into rebuildable videos for docs,
tutorials, release notes, and technical demos.

Instead of recording a one-off screen capture, you write a versioned Markdown
recording script. The script contains prose for readers, YAML directives for
OmegaFlow, terminal commands to run, expected output checks, optional generated
voiceover, and publish targets. OmegaFlow records the terminal session, lines it
up with the script and narration, and writes website-ready assets.

Use it when a demo should live with the code it explains:

- rebuild videos when commands, docs, or setup steps change
- keep terminal demos reviewable in source control
- generate optional voiceover from the same recording script
- publish videos as plain HTML or embedded Docusaurus pages

OmegaFlow is currently supported on Linux, macOS, and WSL on Windows.

## Quick Start

Install OmegaFlow in your project's Python environment:

```bash
python -m pip install omegaflow
```

Create the initial recording workspace:

```bash
omegaflow action=bootstrap
```

Build the generated quickstart recording:

```bash
omegaflow recording=quickstart
```

Watch the result locally:

```bash
omegaflow recording=quickstart action=watch
```

The full guide and reference docs are at
[omegaflow.dev](https://omegaflow.dev/).

## Repository

This repository contains:

- the `omegaflow` Python package and command line tool
- the Docusaurus website for `omegaflow.dev`
- the homepage quickstart demo recording
- tutorial recording scaffolding under `recordings/`

## Development

```bash
nox -s tests
nox -s schema_docs
nox -s package
pnpm --dir website build
omegaflow recording=quickstart-demo
```

The current repository also preserves older OmegaFlow design work under
`docs/future/`.

## Deployment

The website is configured for `https://omegaflow.dev` and includes a GitHub
Pages workflow at `.github/workflows/deploy-website.yml`. The workflow builds
`website/` with pnpm and deploys `website/build`; `website/static/CNAME`
contains the custom domain.
