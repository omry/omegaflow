# OmegaFlow

<p align="center">
  <img src="https://raw.githubusercontent.com/omry/omegaflow/main/docs/design/logo.svg" width="112" alt="OmegaFlow logo" />
</p>

[![PyPI version](https://badge.fury.io/py/omegaflow.svg)](https://badge.fury.io/py/omegaflow)
[![Downloads](https://pepy.tech/badge/omegaflow/month)](https://pepy.tech/project/omegaflow)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/pypi/l/omegaflow.svg)
[![CI](https://github.com/omry/omegaflow/actions/workflows/ci.yml/badge.svg)](https://github.com/omry/omegaflow/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-omegaflow.dev-6eb6ff)](https://omegaflow.dev/)
[![Zulip chat](https://img.shields.io/badge/chat-OmegaFlow%20channel-2e77d0?logo=zulip)](https://hydra-framework.zulipchat.com/#narrow/channel/omegaflow)

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

**[Watch the homepage demo](https://omegaflow.dev/)** ·
[View its recording source](https://github.com/omry/omegaflow/blob/main/recordings/quickstart-demo/index.md)

Recording is supported on Linux and macOS. On Windows, use WSL for the
Linux recording workflow.

## Quick Start

Install OmegaFlow and its managed-browser support in your project's Python
environment, then install the pinned Chromium build used for browser recording
and by `action=watch` outside WSL:

```bash
python -m pip install 'omegaflow[browser]'
python -m playwright install chromium
```

Under WSL, `action=watch` uses Windows Chrome or Edge with an isolated temporary
profile so audio stays on the host instead of crossing the WSLg audio bridge.

OmegaFlow requires Python 3.11+ and Bash. Supported Linux and macOS wheels
include asciinema 3.x. Narrated recordings additionally require `ffmpeg` and
`ffprobe`; generating new narration requires OpenAI API access. See
[Runtime Dependencies](https://omegaflow.dev/runtime-dependencies) for when
each dependency is needed and how recording-specific tools are declared.

Create the initial recording workspace:

```bash
omegaflow action=bootstrap
```

This creates `.omegaflow/config.yaml` and a small recording script at
`recordings/quickstart/index.md`. Edit that Markdown file to define the prose,
commands, checks, and optional narration for your video.

Build the generated quickstart recording:

```bash
omegaflow recording=quickstart
```

Watch the generated terminal video and optional narration locally:

```bash
omegaflow recording=quickstart action=watch
```

Continue with the [quickstart guide](https://omegaflow.dev/docs/tutorial/quickstart)
or browse the [full documentation](https://omegaflow.dev/docs/intro).

## Development

See the [maintainer guide](MAINTAINERS.md) for development setup, local website
instructions, and validation.
