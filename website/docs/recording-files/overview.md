---
sidebar_position: 1
sidebar_label: Overview
slug: /recording-files
---

# Recording Files

By default, a Studio recording workspace is the `recordings/` directory. The
file you edit most is `<recording-dir>/<id>.md`: it is the script for one
video. Projects that keep recordings somewhere else can set that in
[Studio Configuration](../studio-configuration.md).

```bash
studio recording=hello action=bootstrap  # Create the default recording workspace
```

```yaml
recordings/:           # Recording workspace
  config.yaml:         # Workspace defaults for recordings
  hello.md:            # Recording Markdown file for one video
  hello/:              # Per-recording support files
    hello.sh:          # Shell script used by hello.md
  .omegaflow/:         # Default generated runtime state and local outputs
```

## Recording File Structure

A recording Markdown file has three main parts:

| Part | Purpose | Where to read more |
| --- | --- | --- |
| Recording configuration | YAML frontmatter at the top of the file. Defines `id`, `title`, and per-video config overrides. | [Recording Configuration](./config.md) |
| Markdown prose | Human-readable notes and headings for the authored walkthrough. | This page |
| `studio-directive` blocks | Machine-readable scene and beat blocks that Studio records, retimes, checks, and publishes. | [Beat](./beat.md) |

````md
---
id: hello
title: Hello Video
---

# Hello Video

```yaml studio-directive
scene: Hello Video
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line in the terminal.
  actions:
  - commands:
    - run_file: hello/hello.sh
      display: bash hello/hello.sh
```
````

## Scene

Every recording defines one scene. The scene names the video:

```yaml
scene: Hello Video
```

It can also be a mapping:

```yaml
scene:
  title: Hello Video
```

## What You Touch

| How often | File | Purpose |
| --- | --- | --- |
| Most often | `<recording-dir>/<id>.md` | The video script: recording configuration, scene, beats, narration, and commands. |
| Often | `<recording-dir>/<id>/` | Shell scripts and small support files for that recording. |
| Occasionally | `<recording-dir>/config.yaml` | Workspace defaults, such as capture style, output directory, audio provider, or environment key. |
| Rarely | `<studio.data_dir>/` | Generated runs, cache, and local outputs. Defaults to `<recording-dir>/.omegaflow/`; do not edit by hand. |

## Read Next

- [Recording Configuration](./config.md): schema defaults, workspace defaults, and frontmatter overrides.
- [Beat](./beat.md): beat structure, actions, checks, commands, and guide prompts.
- [Publishing And Runtime Output](./publishing-runtime.md): publish surfaces and generated files.
- [Studio Configuration](../studio-configuration.md): tool defaults such as the recording directory and runtime state directory.
