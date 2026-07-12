---
sidebar_label: Runtime Dependencies
---

# Runtime Dependencies

Installing the `omegaflow` Python package installs its Python dependencies.
Some capabilities also invoke external programs or services at runtime.

## Core requirements

| Dependency | Required for | Notes |
| --- | --- | --- |
| Python 3.11 or newer | All OmegaFlow commands | Enforced by the Python package metadata. |
| Linux or macOS | Native terminal recording | On Windows, use WSL for the Linux recording workflow. Native Windows recording is not supported. |
| Bash | Recording terminal sessions | OmegaFlow's generated recording session runs under Bash. |
| asciinema 3.x | Recording and terminal playback | Platform wheels for x86-64 and ARM64 Linux/macOS include a recorder. OmegaFlow otherwise uses `studio.asciinema_path`, then `asciinema` on `PATH`. |

OmegaFlow checks the asciinema version before recording or terminal playback
and reports how to configure or install it when unavailable.

## Narration audio

Narration is optional. Recordings with `audio.enabled: false` do not need these
dependencies.

| Dependency | Required for | Notes |
| --- | --- | --- |
| `ffmpeg` | Combining generated narration segments | OmegaFlow invokes the executable on `PATH` when publishing narration audio. |
| `ffprobe` | Measuring segment and published-audio duration | It is normally installed with `ffmpeg` as part of the FFmpeg distribution. |
| OpenAI API access | Generating new narration and word timestamps | Requires network access and the environment variable named by `audio.env`. Cached audio can be reused without another generation request. |

Verify the local FFmpeg tools with:

```bash
ffmpeg -version
ffprobe -version
```

The default API-key variable is `OPENAI_API_KEY`. Projects can choose another
name in recording configuration:

```yaml
audio:
  enabled: true
  env: OPENAI_OMEGAFLOW_API_KEY
```

Keep API keys in the environment or an ignored `.env` file, not in recording
configuration committed to source control.

## Recording-specific commands

Commands used by a recording—such as `git`, a package manager, or the program
being demonstrated—are project dependencies rather than OmegaFlow package
dependencies. Declare them so OmegaFlow fails before starting capture:

```yaml
requirements:
  commands:
    - git
    - python
```

OmegaFlow checks each declared command against the current process `PATH` and
the directory containing the Python executable running OmegaFlow.

## Optional desktop tools

`action=watch` can open the local player in a graphical browser. A browser is
not required on headless systems: OmegaFlow prints the local URL so it can be
opened elsewhere.

Interactive `action=output` uses `$PAGER`, defaulting to `less`. Redirecting
the command to a file or pipe writes the captured output directly and does not
require a pager.
