# Runtime dependencies

This is a private maintainer note. Keep it out of the published website until
Reploy provides the recording and processing environments.

## Local requirements

| Workflow | Requirements |
| --- | --- |
| OmegaFlow CLI | Python 3.11 or newer |
| Terminal recording | Linux or macOS, Bash, and asciinema 3.x |
| Browser recording | `omegaflow[browser]`, OmegaFlow's pinned Playwright Chromium, `ffmpeg`, and `ffprobe` |
| Watching a build | An installed Chrome, Chromium, Edge, or Brave browser; under WSL, Windows Chrome or Edge |
| Reusing narration | `ffmpeg` and `ffprobe` |
| Generating narration | The narration tools above plus OpenAI API access |

Supported Linux and macOS wheels include asciinema. Windows recording runs
through WSL.

## Browser recording setup

```bash
python -m pip install 'omegaflow[browser]'
python -m playwright install chromium
```

Linux hosts missing Chromium's shared libraries may also need:

```bash
python -m playwright install-deps chromium
```

Browser capture uses the pinned Chromium build. `action=watch` instead launches
a system browser with an isolated temporary profile and an audible-autoplay
override.

## Recording-specific tools

Commands used by a recording belong to that recording. Declare them in its
frontmatter so OmegaFlow can fail before capture:

```yaml
requirements:
  commands:
    - git
    - python
```

Revisit and simplify these requirements when the Reploy execution model is
ready.
