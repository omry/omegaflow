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

## Browser recording

Browser recording and native Linux/macOS `action=watch` use OmegaFlow's managed
Chromium. Install the matching Python extra, then install the pinned Chromium
revision managed by Playwright:

```bash
pip install 'omegaflow[browser]'
python -m playwright install chromium
```

On Linux hosts or containers that do not already contain Chromium's shared
libraries, install them with Playwright's platform helper (usually as root):

```bash
python -m playwright install-deps chromium
```

OmegaFlow pins the Playwright package and Chromium revision together. It fails
with a specific remedy when the extra, browser binary, or host libraries are
missing; using an arbitrary system Chrome is not a supported substitute.

Under WSL, browser recording still uses the pinned Linux Chromium, but
`action=watch` launches Windows Chrome or Edge with an isolated temporary
profile. This keeps watch audio on the Windows host and avoids the WSLg audio
bridge. The autoplay override is applied to that isolated host process.

Published browser states and motion also require FFmpeg tools:

| Dependency | Required for | Required capability |
| --- | --- | --- |
| `ffmpeg` | All browser presentations | Lossless `libwebp` encoder for stable states. |
| `ffmpeg` | Captured browser motion | `libvpx` VP8 encoder for muted WebM clips. |
| `ffprobe` | Browser bundle validation | Image/video dimensions, codec, duration, and absence of audio. |

The build reports missing tools or encoders before publishing. Browser audio is
muted during capture and is not part of the browser media payload.

## Narration audio

Narration is optional. Recordings with `audio.enabled: false` do not need these
dependencies.

| Dependency | Required for | Notes |
| --- | --- | --- |
| `ffmpeg` | Combining generated narration segments | OmegaFlow invokes the executable on `PATH` when publishing narration audio. Browser recording additionally requires the codecs above. |
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

`action=watch` requires a graphical environment because it launches an isolated
Chromium and starts the presentation with audio. Under WSL, Windows Chrome or
Edge must be installed. `action=play` uses the default browser and remains
available when managed autoplay is not needed.

Interactive `action=output` uses `$PAGER`, defaulting to `less`. Redirecting
the command to a file or pipe writes the captured output directly and does not
require a pager.
