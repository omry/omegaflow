---
sidebar_position: 2
sidebar_label: Install And Supported Platforms
slug: /getting-started/install/
---

# Install OmegaFlow

OmegaFlow requires Python 3.11 or newer. Install it in the Python environment
for the project whose recordings you want to build:

```bash
python -m pip install omegaflow
```

The Linux and macOS wheels include the terminal recorder. You do not need to
install asciinema separately when using one of those wheels.

## Add browser recording

Browser beats use OmegaFlow's pinned Playwright Chromium rather than whichever
browser happens to be installed on the machine:

```bash
python -m pip install 'omegaflow[browser]'
python -m playwright install chromium
```

On Linux, Chromium may also need system libraries:

```bash
python -m playwright install-deps chromium
```

The last command may require administrator access. If your distribution does
not support Playwright's installer, install its equivalent Chromium runtime
libraries through the system package manager.

## Install presentation media tools

Install `ffmpeg` and `ffprobe` before building browser recordings or generated
narration. Browser states require the `libwebp` encoder; captured browser motion
also requires the `libx264` H.264 encoder.

On Debian, Ubuntu, and WSL distributions based on them:

```bash
sudo apt-get update
sudo apt-get install ffmpeg
```

On macOS with Homebrew:

```bash
brew install ffmpeg
```

Confirm that the selected FFmpeg build exposes both encoders:

```bash
ffmpeg -hide_banner -encoders | grep -E 'libwebp|libx264'
ffprobe -version
```

## Supported platforms

| Environment | Terminal recording | Browser recording | Local watch |
| --- | --- | --- | --- |
| Linux x86-64 | Supported | Supported with the browser extra, pinned Chromium, and media tools | Opens an installed Chrome, Chromium, Edge, or Brave browser |
| Linux ARM64 | Supported | Supported with the browser extra, pinned Chromium, and media tools | Opens an installed supported system browser |
| macOS Intel | Supported | Supported with the browser extra, pinned Chromium, and media tools | Opens an installed supported system browser |
| macOS Apple silicon | Supported | Supported with the browser extra, pinned Chromium, and media tools | Opens an installed supported system browser |
| Windows through WSL | Supported through the Linux workflow | Supported inside WSL when its Chromium dependencies are available | Opens Windows Chrome or Edge so playback uses the host audio stack |
| Native Windows | Not supported in 0.9 | Not supported in 0.9 | Not a supported recording environment |

The published Linux and macOS wheels cover the architectures listed above and
bundle asciinema 3.x. Native Windows recording and native desktop-application
recording are future capabilities, not part of the current platform contract.

## Narration credential

Generating narration requires OpenAI API access. Project bootstrap creates the
private placeholder `.omegaflow/omegaflow-secret.env`; add the key there:

```dotenv
OPENAI_OMEGAFLOW_API_KEY=your-key
```

CI may provide the same name directly in the parent process environment.
OmegaFlow does not expose this service credential to recorded commands. Secrets
needed by the application being recorded use the separate
[recording-local application-secret contract](../reference/configuration/recordings.md#application-secrets).

## Verify the setup

Create a starter project and build its terminal-only test video:

```bash
omegaflow bootstrap=project
omegaflow recording=test-video action=build
omegaflow recording=test-video action=watch
```

Continue with [Build your first video](./first-video.md), then choose a focused
workflow from [Next steps](./next-steps.md).
