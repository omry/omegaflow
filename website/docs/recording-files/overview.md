---
sidebar_position: 1
sidebar_label: Overview
slug: /recording-files
---

# Recording Files

By default, an OmegaFlow recording workspace is the `recordings/` directory. The
file you edit most is `<recording-dir>/<id>/omegaflow.md`: it is the source for
one video. Recording ids can include nested directories, such as
`tutorial/install`, which maps to
`recordings/tutorial/install/omegaflow.md`. Projects that keep recordings
somewhere else can set that in [OmegaFlow Configuration](../configuration.md).

```bash
omegaflow action=bootstrap  # Create the default quickstart recording
```

```yaml
recordings/:           # Recording workspace
  config.yaml:         # Workspace defaults for recordings
  quickstart/:         # Bootstrap-created video directory
    omegaflow.md:      # Recording Markdown file for one video
    scripts/:          # Per-video support scripts
      hello.sh:        # Shell script used by omegaflow.md
  tutorial/:           # Optional grouping directory
    install/:          # Nested video directory, selected as tutorial/install
      omegaflow.md:    # Recording Markdown file for that video
  .omegaflow/:         # Default generated runtime state and local outputs
```

## Recording File Structure

A recording Markdown file has three main parts:

| Part | Purpose | Where to read more |
| --- | --- | --- |
| Recording configuration | YAML frontmatter at the top of the file. Defines `id`, `title`, and per-video config overrides. | [Recording Configuration](./config.md) |
| Markdown prose | Human-readable notes and headings for the authored walkthrough. | This page |
| `studio-directive` blocks | Machine-readable scene and beat blocks that OmegaFlow records, retimes, checks, and publishes. | [Beat](./beat.md) |

````md
---
id: quickstart
title: Quickstart
---

# Quickstart

```yaml studio-directive
scene: Quickstart
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line in the terminal.
  actions:
  - commands:
    - run_file: scripts/hello.sh
      display: bash scripts/hello.sh
```
````

## Scene

Every recording defines one scene. The scene names the video:

```yaml
scene: Quickstart
```

It can also be a mapping:

```yaml
scene:
  title: Quickstart
```

## What You Touch

| How often | File | Purpose |
| --- | --- | --- |
| Most often | `<recording-dir>/<id>/omegaflow.md` | The video source: recording configuration, scene, beats, narration, and commands. Nested ids such as `tutorial/install` are supported. |
| Often | `<recording-dir>/<id>/scripts/` | Shell scripts and small support files for that recording. |
| Occasionally | `<recording-dir>/config.yaml` | Workspace defaults, such as capture style, output directory, audio provider, or environment key. |
| Rarely | `<studio.data_dir>/` | Generated runs, cache, and local outputs. Defaults to `<recording-dir>/.omegaflow/`; do not edit by hand. |

## Read Next

- [Recording Configuration](./config.md): schema defaults, workspace defaults, and frontmatter overrides.
- [Beat](./beat.md): beat structure, actions, checks, commands, and guide prompts.
- [Publishing And Runtime Output](./publishing-runtime.md): publish surfaces and generated files.
- [OmegaFlow Configuration](../configuration.md): tool defaults such as the recording directory and runtime state directory.
