---
sidebar_position: 1
sidebar_label: Overview
slug: /recording-files
---

# Recording Files

By default, an OmegaFlow recording workspace is the `recordings/` directory. The
file you edit most is `<recording-dir>/<id>/index.md`: it is usually the source
for one video. Recording ids can include nested directories, such as
`tutorial/install`, which maps to
`recordings/tutorial/install/index.md`. Projects that keep recordings
somewhere else can set that in [Project Configuration](../configuration.md).

```bash
omegaflow action=bootstrap  # Create the default quickstart recording
```

```yaml
.omegaflow/:          # Project-local OmegaFlow tool config
  config.yaml:        # Tool defaults such as studio.recording_dir
recordings/:           # Recording workspace
  config.yaml:         # Workspace defaults for recordings
  quickstart/:         # Bootstrap-created video directory
    index.md:          # Recording Markdown file for one video
  tutorial/:           # Optional grouping directory
    index.md:           # Optional collection that builds child videos in order
    install/:          # Nested video directory, selected as tutorial/install
      index.md:        # Recording Markdown file for that video
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
kind: video
id: quickstart
title: Quickstart
---

# Quickstart

```yaml studio-directive
scene: Quickstart
```

```yaml studio-directive
beat:
  id: navigate-sections
  heading: Navigate By Section
  narration: Each beat becomes a section in the generated player.
  viewer_hold: 3
  actions:
  - commands:
    - id: explain_sections
      run: printf 'Every beat becomes a section in the player.\\n'
      expect:
        output_contains:
        - Every beat becomes a section in the player.
```

```yaml studio-directive
beat:
  id: control-playback
  heading: Control Playback
  narration: Preview sections and adjust playback speed in the generated player.
  viewer_hold: 4
  actions:
  - commands:
    - id: explain_playback
      run: printf 'Hover the timeline to preview sections.\nAdjust playback speed when you need it.\n'
      expect:
        output_contains:
        - Hover the timeline to preview sections.
        - Adjust playback speed when you need it.
```
````

## Recording Collections

An `index.md` can instead define an ordered build shortcut:

```yaml
---
kind: collection
id: tutorial
title: Tutorial
members:
  - tutorial/recording-file
  - tutorial/beat
  - tutorial/publishing
---
```

Running `omegaflow recording=tutorial` validates every member, then builds the
videos sequentially using the normal video pipeline. A collection does not
produce a video or recording run of its own. `dry_run=true` lists its members.
After the videos are built, `omegaflow recording=tutorial action=watch` serves
one index page using each member's `title` and `description`; selecting a card
opens that member's normal player. Other single-video actions require selecting
one member.

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
| Most often | `<recording-dir>/<id>/index.md` | A video source, or an ordered collection of video ids. Nested ids such as `tutorial/install` are supported. |
| Often | `<recording-dir>/<id>/scripts/` | Shell scripts and small support files for that recording. |
| Occasionally | `<recording-dir>/config.yaml` | Workspace defaults, such as capture style, output directory, audio provider, or environment key. |
| Rarely | `<studio.data_dir>/` | Generated runs, cache, and local outputs. Defaults to `<recording-dir>/.omegaflow/`; do not edit by hand. |

## Read Next

- [Recording Configuration](./config.md): schema defaults, workspace defaults, and frontmatter overrides.
- [Beat](./beat.md): beat structure, actions, checks, commands, and guide prompts.
- [Publishing And Runtime Output](./publishing-runtime.md): publish surfaces and generated files.
- [Project Configuration](../configuration.md): tool defaults such as the recording directory and runtime state directory.
